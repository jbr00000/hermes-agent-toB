"""语义分块（semantic_chunker）测试：全部 hermetic，embedding 用确定性假向量。

假 embedder 按关键词返回话题向量（含"氢/燃料"→话题A 向量，含"财务/审计"→
话题B 向量），使滑窗相似度在话题切换处必然出现极小值，从而可以断言切点位置
而不断言具体数值快照。
"""
from __future__ import annotations

import numpy as np
import pytest

from server.deployment_config import SemanticChunkingConfig
from server.knowledge.chunker import chunk_document, count_tokens
from server.knowledge.semantic_chunker import (
    _correlate,
    _filter_split_indices,
    _find_local_minima,
    _savgol_coeffs,
    load_sentence_delimiters,
    semantic_split,
    split_sentences,
)


# -------------------------------------------------------------- 句子切分


def test_load_sentence_delimiters_from_bundled_template() -> None:
    delimiters, include_delim = load_sentence_delimiters()
    assert "。" in delimiters and "；" in delimiters
    assert include_delim == "prev"


def test_split_sentences_keeps_delimiter_with_previous() -> None:
    text = "第一句足够长所以不会被合并掉。第二句也足够长所以同样不会被合并！"
    sentences = split_sentences(text, ["。", "！", "\n"], min_chars=1)
    assert sentences[0].endswith("。")
    assert sentences[1].endswith("！")


def test_split_sentences_merges_short_sentences_forward() -> None:
    long_a = "这是一个足够长的句子用来满足最小字符数限制的要求。"
    long_b = "另一个足够长的句子同样满足最小字符数限制的要求。"
    sentences = split_sentences(f"{long_a}好。{long_b}", ["。", "\n"], min_chars=24)
    # "好。"（3 字符 < 24）并入前一句
    assert any(s.endswith("好。") for s in sentences)
    assert not any(s == "好。" for s in sentences)


# ------------------------------------------------------- Savitzky-Golay


def test_savgol_coeffs_smooths_linear_data_to_itself() -> None:
    # 与 chonkie-core Rust 单测同款断言：线性数据经平滑滤波保持线性
    data = np.arange(1, 10, dtype=np.float64)
    smoothed = _correlate(data, _savgol_coeffs(5, 2, 0))
    for i, val in enumerate(smoothed):
        assert abs(val - (i + 1.0)) < 0.5


def test_savgol_first_derivative_coeffs_match_reference() -> None:
    # 参考值：三次拟合 5 点一阶导数模板 [1,-8,0,8,-1]/12（经典五点Stencil，
    # 与 scipy.signal.savgol_coeffs(5, 3, deriv=1) 同值、顺序相反——scipy 返回卷积序）
    coeffs = _savgol_coeffs(5, 3, 1)
    assert list(coeffs) == pytest.approx([1 / 12, -2 / 3, 0.0, 2 / 3, -1 / 12])


def test_savgol_second_derivative_recovers_quadratic_curvature() -> None:
    # y = 3x² - 2x + 1 的二阶导恒为 6（镜像边界可能失真，只断言内部点）
    data = np.asarray([3.0 * i * i - 2.0 * i + 1.0 for i in range(15)])
    curvature = _correlate(data, _savgol_coeffs(5, 3, 2))
    assert list(curvature[2:-2]) == pytest.approx([6.0] * 11)


def test_savgol_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        _savgol_coeffs(4, 2, 0)  # 偶数窗口
    with pytest.raises(ValueError):
        _savgol_coeffs(3, 3, 0)  # 窗口 ≤ 阶数


def test_find_local_minima_on_parabola() -> None:
    # 抛物线在 i=10 处有唯一极小值（对齐 chonkie-core Rust 单测的构造）
    data = np.asarray([((i - 10.0) / 3.0) ** 2 for i in range(20)])
    indices, values = _find_local_minima(data, window=5, polyorder=2, tolerance=0.5)
    assert indices
    assert abs(indices[0] - 10) <= 2


def test_find_local_minima_short_series_returns_empty() -> None:
    indices, _ = _find_local_minima(np.asarray([0.5, 0.4, 0.5]), window=5, polyorder=3, tolerance=0.2)
    assert indices == []


# ----------------------------------------------------------- 分位数过滤


def test_filter_split_indices_percentile_and_min_distance() -> None:
    indices = [0, 5, 8, 15, 20]
    values = [0.1, 0.3, 0.2, 0.5, 0.4]
    # 50% 分位数 = 0.3 → 保留值 ≤ 0.3 的切点；min_distance=3 再过滤过近切点
    kept = _filter_split_indices(indices, values, threshold=0.5, min_distance=3)
    assert kept
    assert all(values[indices.index(i)] <= 0.3 for i in kept)
    assert all(b - a >= 3 for a, b in zip(kept, kept[1:]))


def test_filter_split_indices_empty() -> None:
    assert _filter_split_indices([], [], 0.8, 1) == []


# ----------------------------------------------------------- 端到端分组

_TOPIC_A = np.asarray([1.0] + [0.0] * 15)
_TOPIC_B = np.asarray([0.0, 1.0] + [0.0] * 14)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """话题 A（氢/燃料）与话题 B（财务/审计）返回近似正交的向量。"""
    vectors = []
    for text in texts:
        a_hits = sum(text.count(k) for k in ("氢", "燃料"))
        b_hits = sum(text.count(k) for k in ("财务", "审计"))
        vec = _TOPIC_A * a_hits + _TOPIC_B * b_hits
        if not vec.any():
            vec = _TOPIC_A + _TOPIC_B
        vectors.append((vec / np.linalg.norm(vec)).tolist())
    return vectors


def _topic_sentences(topic: str, n: int) -> list[str]:
    # 话题词都取 ≥13 字，保证整句 ≥24 字符，不被 min_characters_per_sentence 合并
    words = "氢燃料电池系统的热管理设计" if topic == "a" else "财务审计报告的合规性审查流程"
    return [f"{words}的第{i}项详细说明内容。" for i in range(n)]


# 双话题过渡句：真实文档的话题切换是渐变的，相似度曲线呈平缓谷底
# （而不是一步到底的 V 形），默认 filter_tolerance 即可检出
_TRANSITION = "氢燃料电池系统的财务审计合规性流程详细说明内容。"


def test_semantic_split_cuts_at_topic_boundary() -> None:
    text = "".join(_topic_sentences("a", 5) + [_TRANSITION] + _topic_sentences("b", 5))
    chunks = semantic_split(text, _fake_embed, chunk_size=10000, config=SemanticChunkingConfig())
    assert len(chunks) == 2
    # 话题 A 的正文句不与话题 B 的正文句同块（过渡句归任一侧都合理）
    for chunk in chunks:
        has_a = "热管理设计" in chunk
        has_b = "审查流程" in chunk
        assert not (has_a and has_b), f"话题被混进同一块: {chunk[:60]}"


def test_semantic_split_single_topic_stays_one_chunk() -> None:
    text = "".join(_topic_sentences("a", 8))
    chunks = semantic_split(text, _fake_embed, chunk_size=10000, config=SemanticChunkingConfig())
    assert len(chunks) == 1
    assert chunks[0] == text


def test_semantic_split_enforces_chunk_size() -> None:
    text = "".join(_topic_sentences("a", 10))
    chunk_size = count_tokens("".join(_topic_sentences("a", 3)))
    chunks = semantic_split(text, _fake_embed, chunk_size=chunk_size, config=SemanticChunkingConfig())
    assert len(chunks) > 1
    assert all(count_tokens(c) <= chunk_size for c in chunks)


def test_semantic_split_too_few_sentences_single_group() -> None:
    text = "".join(_topic_sentences("a", 2))
    chunks = semantic_split(text, _fake_embed, chunk_size=10000, config=SemanticChunkingConfig())
    assert chunks == [text]


def test_semantic_split_empty_text() -> None:
    assert semantic_split("", _fake_embed, chunk_size=100) == []


# ---------------------------------------------------- chunker 模式集成


def _text_item(text: str, level: int | None = None) -> dict:
    item = {"type": "text", "text": text}
    if level is not None:
        item["text_level"] = level
    return item


def test_chunk_document_semantic_mode_keeps_titles_and_tables() -> None:
    body = "".join(_topic_sentences("a", 5) + [_TRANSITION] + _topic_sentences("b", 5))
    content_list = [
        _text_item("第一章 总则", level=1),
        _text_item(body),
        {"type": "table", "table_body": "<table><tr><td>指标</td></tr></table>", "table_caption": ["参数表"]},
    ]
    chunks = chunk_document(
        content_list, chunk_size=10000, mode="semantic", embed_batch=_fake_embed
    )
    assert all(c.chunk_title.startswith("第一章 总则") for c in chunks)
    table_chunks = [c for c in chunks if "<tr" in c.content]
    assert len(table_chunks) == 1  # 表格原子，不经语义切分
    text_chunks = [c for c in chunks if "<tr" not in c.content]
    assert len(text_chunks) >= 2  # 两个话题被切开
    assert [c.doc_pos for c in chunks] == list(range(len(chunks)))


def test_chunk_document_semantic_mode_requires_embed_batch() -> None:
    with pytest.raises(ValueError, match="embed_batch"):
        chunk_document([_text_item("正文")], mode="semantic")


def test_chunk_document_default_mode_unchanged_without_embed() -> None:
    # structural 默认模式不接受也不需要 embed_batch
    chunks = chunk_document([_text_item("一段普通的正文内容。" * 5)])
    assert len(chunks) == 1
