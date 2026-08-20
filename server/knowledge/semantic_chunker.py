"""语义分块：chonkie ``SemanticChunker`` 的本地化实现（不引 chonkie 依赖）。

参考项目用的是 chonkie ``LateChunker`` + 本地 embedding 模型；我们把它换成
与远程 embedding 端点架构相容的**真·语义分块**，算法逐步对齐 chonkie 1.7 的
SemanticChunker（python 壳）+ chonkie-core（Rust，``src/savgol.rs``）：

1. **句子切分**：按分块模版（``split_templates/default_ch.json``，chonkie
   recipe 格式）的 ``delimiters`` 切句，短句（< ``min_characters_per_sentence``
   字符）向前合并。
2. **滑窗相似度**：``similarities[i] = cosine(embed(第 i..i+window 句拼接),
   embed(第 i+window 句))``——衡量"下一句"是否还属于上文话题。
3. **极小值检测**：对相似度曲线做 Savitzky-Golay 一阶/二阶导数，
   |一阶导| < tolerance 且二阶导 > 0 处为局部极小（话题切换点）。
4. **分位数过滤**：只保留取值 ≤ 极小值集合 ``threshold`` 分位数的极小值，
   且两个切点间距 ≥ ``min_sentences_per_chunk``。
5. **尺寸兜底**：语义组超 ``chunk_size`` 时按句贪心装包硬切。

embedding 全部走客户自部署的 OpenAI 兼容端点（与入库共用 ``Embedder``），
无任何本地模型权重。
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import math
from pathlib import Path
import re

import numpy as np
import tiktoken

from server.deployment_config import SemanticChunkingConfig

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "split_templates" / "default_ch.json"

_ENCODING = None


def _encoding():
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


# ---------------------------------------------------------------- 句子切分


def load_sentence_delimiters(template_path: Path | None = None) -> tuple[list[str], str]:
    """从 chonkie recipe 模版读取句子分隔符与归属方式（include_delim）。"""
    path = template_path or _TEMPLATE_PATH
    recipe = json.loads(path.read_text(encoding="utf-8"))["recipe"]
    delimiters = [str(d) for d in recipe["delimiters"] if d]
    include_delim = str(recipe.get("include_delim") or "prev")
    if not delimiters:
        raise ValueError(f"分块模版没有 delimiters: {path}")
    return delimiters, include_delim


def split_sentences(
    text: str,
    delimiters: list[str],
    *,
    include_delim: str = "prev",
    min_chars: int = 24,
) -> list[str]:
    """按分隔符切句（分隔符归入前/后句），再把过短的句子并进相邻句。"""
    by_length = sorted(delimiters, key=lambda d: len(d), reverse=True)
    pattern = "(" + "|".join(re.escape(d) for d in by_length) + ")"
    parts = re.split(pattern, text)
    sentences: list[str] = []
    if include_delim == "next":
        pending = ""
        for part in parts:
            if not part:
                continue
            if part in delimiters:
                pending = part
                continue
            sentences.append(pending + part)
            pending = ""
        if pending:
            sentences.append(pending)
    else:  # prev：分隔符贴在前一句末尾
        for part in parts:
            if not part:
                continue
            if part in delimiters:
                if sentences:
                    sentences[-1] += part
                else:
                    sentences.append(part)
            else:
                sentences.append(part)
    sentences = [s for s in sentences if s.strip()]
    return _merge_short(sentences, min_chars)


def _merge_short(sentences: list[str], min_chars: int) -> list[str]:
    """短句并入前一句；开头没有前句可并时暂存，并给下一句。"""
    if min_chars <= 1:
        return sentences
    merged: list[str] = []
    for sentence in sentences:
        if merged and len(sentence) < min_chars:
            merged[-1] += sentence
        else:
            merged.append(sentence)
    # 首句若因过短被孤立（前面无可并对象），并给后一句
    if len(merged) >= 2 and len(merged[0]) < min_chars:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


# ---------------------------------------------------------- Savitzky-Golay


def _savgol_coeffs(window: int, polyorder: int, deriv: int) -> np.ndarray:
    """Savitzky-Golay 系数（Vandermonde 最小二乘，与 chonkie-core Rust 同算法）。"""
    if window % 2 == 0 or window <= polyorder:
        raise ValueError(f"非法 savgol 参数: window={window}, polyorder={polyorder}")
    half = (window - 1) // 2
    x = np.arange(window, dtype=np.float64) - half
    vandermonde = np.vander(x, polyorder + 1, increasing=True)
    ata_inv = np.linalg.inv(vandermonde.T @ vandermonde)
    factorial = math.factorial(deriv)
    return factorial * (vandermonde @ ata_inv[deriv])


def _correlate(data: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """与 kernel 的相关运算（核不翻转），边界镜像反射（对齐 chonkie-core 的 apply_convolution）。"""
    half = len(kernel) // 2
    padded = np.pad(data, half, mode="reflect")
    # np.convolve 会翻转核，预先翻回即为相关
    return np.convolve(padded, kernel[::-1], mode="valid")


def _find_local_minima(
    similarities: np.ndarray,
    *,
    window: int,
    polyorder: int,
    tolerance: float,
) -> tuple[list[int], list[float]]:
    """局部极小值：|一阶导| < tolerance 且二阶导 > 0（曲线触底回升处）。"""
    if len(similarities) < window:
        return [], []
    first_deriv = _correlate(similarities, _savgol_coeffs(window, polyorder, 1))
    second_deriv = _correlate(similarities, _savgol_coeffs(window, polyorder, 2))
    indices: list[int] = []
    values: list[float] = []
    for i in range(len(similarities)):
        if abs(first_deriv[i]) < tolerance and second_deriv[i] > 0:
            indices.append(i)
            values.append(float(similarities[i]))
    return indices, values


def _filter_split_indices(
    indices: list[int],
    values: list[float],
    threshold: float,
    min_distance: int,
) -> list[int]:
    """只保留 ≤ threshold 分位数的极小值，且相邻切点间距 ≥ min_distance。"""
    if not indices:
        return []
    threshold_val = float(np.percentile(values, threshold * 100))
    kept: list[int] = []
    last: int | None = None
    for idx, val in zip(indices, values):
        distance_ok = last is None or idx >= last + min_distance
        if val <= threshold_val and distance_ok:
            kept.append(idx)
            last = idx
    return kept


# ---------------------------------------------------------------- 语义分组


def semantic_split(
    text: str,
    embed_batch: Callable[[list[str]], list[list[float]]],
    chunk_size: int,
    config: SemanticChunkingConfig | None = None,
) -> list[str]:
    """把一段文本按语义切成 ≤ ``chunk_size`` token 的若干块。

    ``embed_batch`` 与入库共用同一个 Embedder.embed——分块阶段的句级
    embedding 是语义模式的固有成本（chonkie 同样每窗一句各 embed 一次）。
    """
    cfg = config or SemanticChunkingConfig()
    delimiters, include_delim = load_sentence_delimiters()
    sentences = split_sentences(
        text,
        delimiters,
        include_delim=include_delim,
        min_chars=cfg.min_characters_per_sentence,
    )
    if not sentences:
        return []
    window = cfg.similarity_window
    if len(sentences) <= window:
        return _pack_by_size([sentences], chunk_size)

    window_texts = ["".join(sentences[i : i + window]) for i in range(len(sentences) - window)]
    next_texts = sentences[window:]
    window_embeddings = embed_batch(window_texts)
    next_embeddings = embed_batch(next_texts)
    similarities = _cosine_pairs(window_embeddings, next_embeddings)

    indices, values = _find_local_minima(
        similarities,
        window=cfg.filter_window,
        polyorder=cfg.filter_polyorder,
        tolerance=cfg.filter_tolerance,
    )
    split_at = _filter_split_indices(indices, values, cfg.threshold, cfg.min_sentences_per_chunk)
    boundaries = [0] + [i + window for i in split_at] + [len(sentences)]
    groups = [
        sentences[boundaries[i] : boundaries[i + 1]] for i in range(len(boundaries) - 1)
    ]
    groups = [group for group in groups if group]
    chunks = _pack_by_size(groups, chunk_size)
    logger.debug(
        "semantic_split: %d 句 → %d 个切点 → %d 块", len(sentences), len(split_at), len(chunks)
    )
    return chunks


def _cosine_pairs(
    window_embeddings: list[list[float]], next_embeddings: list[list[float]]
) -> np.ndarray:
    windows = np.asarray(window_embeddings, dtype=np.float64)
    nexts = np.asarray(next_embeddings, dtype=np.float64)
    dot = np.einsum("ij,ij->i", windows, nexts)
    norms = np.linalg.norm(windows, axis=1) * np.linalg.norm(nexts, axis=1)
    norms[norms == 0] = 1.0  # 零向量兜底，避免除零
    return dot / norms


def _pack_by_size(groups: list[list[str]], chunk_size: int) -> list[str]:
    """语义组的 chunk_size 兜底：超尺寸组按句贪心装包（对齐 chonkie _split_groups）。"""
    chunks: list[str] = []
    for group in groups:
        current: list[str] = []
        current_tokens = 0
        for sentence in group:
            tokens = len(_encoding().encode(sentence))
            if current and current_tokens + tokens > chunk_size:
                chunks.append("".join(current))
                current = []
                current_tokens = 0
            current.append(sentence)
            current_tokens += tokens
        if current:
            chunks.append("".join(current))
    return chunks
