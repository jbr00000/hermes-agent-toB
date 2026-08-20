"""Chunker for knowledge documents.

Pure functions over the MinerU-style ``content_list`` produced by
``parser_client``. 两种模式（``deployment.yaml: knowledge.chunk_mode``）：

- **structural**（默认）：标题分段 + tiktoken 递归切分，零 embedding 消耗。
- **semantic**：标题分段保持不变，段内用句级 embedding 相似度找话题切换点
  （chonkie SemanticChunker 的本地化实现，见 ``semantic_chunker.py``）。

公共策略（两种模式一致）：

1. **结构分段**：遍历 content_list，``text_level`` 标题项开启新 section，
   chunk_title = 最近标题路径（截 200 字符）。
2. **段内切分**：structural 走段落→句子递归 + ``chunk_overlap`` 重叠；
   semantic 走滑窗相似度 + Savitzky-Golay 极小值检测（无重叠）。
   短于 ``min_tail_tokens``（deployment.yaml ``min_chunk_tokens``，默认 50）
   的尾块向前合并。
3. **表格**：``type=="table"`` 整块成一个 chunk；超 ``2×chunk_size`` 按行切
   并重复表头行；标题追加"（表）"。
4. 页眉/页脚降级为普通文本；页码/页脚注丢弃；图片只保留 caption。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

import tiktoken

from server.deployment_config import DEFAULT_MIN_CHUNK_TOKENS, SemanticChunkingConfig

from .semantic_chunker import semantic_split

DEFAULT_CHUNK_SIZE = 400
DEFAULT_CHUNK_OVERLAP = 64
_MIN_TAIL_TOKENS = DEFAULT_MIN_CHUNK_TOKENS  # 比这还短的尾块并回前一块
_TITLE_MAX_CHARS = 200

_HEADING_TYPES = {"text"}
_SKIP_TYPES = {"page_number", "page_footnote"}
_TABLE_SPLIT_FACTOR = 2

_ENCODING = None


def _encoding():
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


@dataclass
class Chunk:
    """One chunk ready for persistence / embedding."""

    content: str
    chunk_title: str
    doc_pos: int
    token_num: int


def chunk_document(
    content_list: list[dict[str, Any]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    mode: str = "structural",
    embed_batch: Callable[[list[str]], list[list[float]]] | None = None,
    semantic: SemanticChunkingConfig | None = None,
    min_tail_tokens: int = _MIN_TAIL_TOKENS,
) -> list[Chunk]:
    """Chunk a parsed document. ``doc_pos`` is a global 0-based sequence.

    ``mode="structural"``（默认）：标题分段 + token 递归切分，零 embedding 消耗。
    ``mode="semantic"``：标题分段保持不变，段内改用句级 embedding 相似度
    找话题切换点（见 semantic_chunker），此时必须提供 ``embed_batch``。
    ``min_tail_tokens``：短于此 token 数的尾块并入前一块（表格块除外）。
    """
    if mode == "semantic" and embed_batch is None:
        raise ValueError("chunk_mode=semantic 需要 embed_batch（embedding 端点未配置？）")
    sections = _to_sections(content_list)
    chunks: list[Chunk] = []
    for title, blocks in sections:
        chunks.extend(
            _chunk_section(title, blocks, chunk_size, chunk_overlap, mode, embed_batch, semantic)
        )
    chunks = _merge_short_tails(chunks, min_tail_tokens)
    for pos, chunk in enumerate(chunks):
        chunk.doc_pos = pos
        chunk.token_num = count_tokens(chunk.content)
    return chunks


# ---------------------------------------------------------------- 结构分段


def _to_sections(content_list: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group content items into (chunk_title, blocks) sections.

    ``chunk_title`` 取最近的标题路径（如 "第一章 / 1.2 背景"），表格/图片块
    跟随其所在的当前 section。
    """
    sections: list[tuple[str, list[dict[str, Any]]]] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text)
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            sections.append((_title_path(heading_stack), current))
            current = []

    for item in content_list:
        item_type = item.get("type")
        if item_type in _SKIP_TYPES:
            continue
        if item_type in _HEADING_TYPES and item.get("text_level"):
            level = int(item["text_level"])
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            flush()
            heading_stack = [(lv, tx) for lv, tx in heading_stack if lv < level]
            heading_stack.append((level, text))
            continue
        normalized = _normalize_block(item)
        if normalized is not None:
            current.append(normalized)
    flush()
    return sections


def _title_path(heading_stack: list[tuple[int, str]]) -> str:
    path = " / ".join(text for _, text in heading_stack)
    return path[:_TITLE_MAX_CHARS]


def _normalize_block(item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = item.get("type")
    if item_type == "table":
        body = str(item.get("table_body") or "").strip()
        if not body:
            return None
        captions = item.get("table_caption") or []
        caption = "；".join(str(c) for c in captions if c)
        return {"kind": "table", "html": body, "caption": caption}
    if item_type == "image":
        captions = item.get("img_caption") or item.get("image_caption") or []
        caption = "；".join(str(c) for c in captions if c).strip()
        if not caption:
            return None
        return {"kind": "text", "text": f"[图] {caption}"}
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    return {"kind": "text", "text": text}


# ---------------------------------------------------------------- 递归切分


def _chunk_section(
    title: str,
    blocks: list[dict[str, Any]],
    chunk_size: int,
    chunk_overlap: int,
    mode: str = "structural",
    embed_batch: Callable[[list[str]], list[list[float]]] | None = None,
    semantic: SemanticChunkingConfig | None = None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    pending_text: list[str] = []

    def flush_text() -> None:
        nonlocal pending_text
        text = "\n\n".join(pending_text).strip()
        pending_text = []
        if not text:
            return
        if mode == "semantic":
            # embed_batch 一定非 None（chunk_document 入口已校验）
            for piece in semantic_split(text, embed_batch, chunk_size, semantic):  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
                chunks.append(Chunk(content=piece, chunk_title=title, doc_pos=-1, token_num=0))
        else:
            chunks.extend(_split_text(text, title, chunk_size, chunk_overlap))

    for block in blocks:
        if block["kind"] == "table":
            flush_text()
            table_title = f"{title}（表）" if title else "（表）"
            caption = block["caption"]
            chunks.extend(
                _split_table(block["html"], caption, table_title, chunk_size)
            )
        else:
            pending_text.append(block["text"])
    flush_text()
    return chunks


def _split_text(text: str, title: str, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    """Paragraph → sentence recursive split with token overlap."""
    if count_tokens(text) <= chunk_size:
        return [Chunk(content=text, chunk_title=title, doc_pos=-1, token_num=0)]

    pieces = _split_into_pieces(text, chunk_size)
    return _pack_pieces(pieces, title, chunk_size, chunk_overlap)


def _split_into_pieces(text: str, chunk_size: int) -> list[str]:
    """Split text into pieces each ≤ chunk_size (paragraph first, then sentence)."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if count_tokens(paragraph) <= chunk_size:
            pieces.append(paragraph)
            continue
        sentences = [
            s for s in re.split(r"(?<=[。！？!?；;])\s*|(?<=[.!?])\s+", paragraph) if s.strip()
        ]
        for sentence in sentences:
            sentence = sentence.strip()
            if count_tokens(sentence) <= chunk_size:
                pieces.append(sentence)
            else:  # 极端长句：按 token 硬切
                pieces.extend(_hard_split(sentence, chunk_size))
    return pieces


def _hard_split(text: str, chunk_size: int) -> list[str]:
    encoding = _encoding()
    tokens = encoding.encode(text)
    return [
        encoding.decode(tokens[start : start + chunk_size])
        for start in range(0, len(tokens), chunk_size)
    ]


def _pack_pieces(
    pieces: list[str], title: str, chunk_size: int, chunk_overlap: int
) -> list[Chunk]:
    """Greedy-pack pieces into ≤chunk_size chunks with token-level overlap."""
    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    def emit() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(
                Chunk(content="\n\n".join(current), chunk_title=title, doc_pos=-1, token_num=0)
            )

    for piece in pieces:
        piece_tokens = count_tokens(piece)
        if current and current_tokens + piece_tokens > chunk_size:
            emit()
            overlap_text = _tail_overlap(chunks[-1].content, chunk_overlap)
            current = [overlap_text, piece] if overlap_text else [piece]
            current_tokens = count_tokens(current[0]) + piece_tokens
        else:
            current.append(piece)
            current_tokens += piece_tokens
    emit()
    return chunks


def _tail_overlap(text: str, chunk_overlap: int) -> str:
    if chunk_overlap <= 0:
        return ""
    encoding = _encoding()
    tokens = encoding.encode(text)
    if len(tokens) <= chunk_overlap:
        return ""
    return encoding.decode(tokens[-chunk_overlap:]).strip()


# --------------------------------------------------------------------- 表格


def _split_table(html_body: str, caption: str, title: str, chunk_size: int) -> list[Chunk]:
    """Table whole-block; oversized tables split by row with header repeated."""
    content = f"{caption}\n{html_body}" if caption else html_body
    if count_tokens(content) <= chunk_size * _TABLE_SPLIT_FACTOR:
        return [Chunk(content=content, chunk_title=title, doc_pos=-1, token_num=0)]

    rows = re.split(r"(?<=</tr>)", html_body)
    rows = [row for row in rows if row.strip()]
    if not rows:
        return [Chunk(content=content, chunk_title=title, doc_pos=-1, token_num=0)]
    header, body_rows = rows[0], rows[1:]

    chunks: list[Chunk] = []
    current: list[str] = []
    for row in body_rows:
        candidate = "".join([header, *current, row])
        if current and count_tokens(candidate) > chunk_size * _TABLE_SPLIT_FACTOR:
            chunks.append(_table_chunk(header, current, caption, title))
            current = []
        current.append(row)
    if current:
        chunks.append(_table_chunk(header, current, caption, title))
    return chunks


def _table_chunk(header: str, rows: list[str], caption: str, title: str) -> Chunk:
    body = "".join([header, *rows])
    content = f"{caption}\n{body}" if caption else body
    return Chunk(content=content, chunk_title=title, doc_pos=-1, token_num=0)


# ----------------------------------------------------------------- 尾块合并


def _merge_short_tails(chunks: list[Chunk], min_tokens: int = _MIN_TAIL_TOKENS) -> list[Chunk]:
    """Merge chunks shorter than ``min_tokens`` into the previous chunk.

    表格块保持原子性（含 ``<tr>`` 的内容不并入文本块，也不吸收别块）。
    """
    if len(chunks) < 2:
        return chunks
    merged: list[Chunk] = [chunks[0]]
    for chunk in chunks[1:]:
        is_table = "<tr" in chunk.content
        if not is_table and count_tokens(chunk.content) < min_tokens and merged:
            merged[-1].content = f"{merged[-1].content}\n\n{chunk.content}"
        else:
            merged.append(chunk)
    return merged
