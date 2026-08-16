"""Knowledge-base search tool — the agent's window onto the enterprise KB.

Hybrid retrieval (ES BM25 + Milvus vector, RRF+weighted fusion, MySQL
hydration) lives in ``server/knowledge/retriever.py``; this module is only
the tool surface. Gated by ``check_fn`` on the deployment config — a
deployment without ``knowledge.enabled`` + ES/Milvus/embedding never sees
the tool (same pattern as kanban tools).
"""
from __future__ import annotations

import logging
from typing import Any

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_MAX_TOPK = 20
_SNIPPET_CONTENT_MAX_CHARS = 4000  # 防御：单块过长时截断，避免一次检索撑爆上下文


def _knowledge_config():
    try:
        from server.deployment_config import load_deployment_config

        return load_deployment_config().knowledge
    except Exception:
        return None


def _check_knowledge_enabled() -> bool:
    cfg = _knowledge_config()
    if cfg is None or not cfg.enabled:
        return False
    return bool(cfg.es_url and cfg.milvus_uri and cfg.embedding.base_url)


def knowledge_search(
    query: str,
    kb_id: str | None = None,
    topk: int | None = None,
) -> str:
    """Search the enterprise knowledge base; returns JSON with ranked chunks."""
    text = str(query or "").strip()
    if not text:
        return tool_error("knowledge_search 需要非空的 query")

    cfg = _knowledge_config()
    if cfg is None or not cfg.enabled:
        return tool_error("知识库未启用（deployment.yaml knowledge.enabled=false）")

    effective_kb = str(kb_id).strip() if kb_id else None
    if effective_kb:
        from server.storage import get_repository

        if get_repository().get_knowledge_base(effective_kb) is None:
            return tool_error(f"知识库不存在: {effective_kb}")

    limit = cfg.retrieval.topk
    if topk:
        try:
            limit = max(1, min(int(topk), _MAX_TOPK))
        except (TypeError, ValueError):
            pass

    from server.knowledge.retriever import search_chunks

    chunks = search_chunks(text, kb_id=effective_kb, topk=limit, config=cfg)
    if not chunks:
        return tool_result(
            {
                "query": text,
                "total": 0,
                "chunks": [],
                "message": "知识库中未检索到相关内容，请如实告知用户，不要编造。",
            }
        )
    return tool_result(
        {
            "query": text,
            "total": len(chunks),
            "chunks": [
                {
                    "num": index,
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "doc_name": chunk["doc_name"],
                    "chunk_title": chunk["chunk_title"],
                    "content": str(chunk["content"])[:_SNIPPET_CONTENT_MAX_CHARS],
                    "score": chunk["score"],
                }
                for index, chunk in enumerate(chunks, start=1)
            ],
        }
    )


def _handle(args: dict, **kw: Any) -> str:
    try:
        return knowledge_search(
            query=str(args.get("query") or ""),
            kb_id=args.get("kb_id"),
            topk=args.get("topk"),
        )
    except Exception as exc:
        logger.warning("knowledge_search failed: %s", exc)
        return tool_error(f"知识库检索失败: {exc}")


registry.register(
    name="knowledge_search",
    toolset="knowledge",
    schema={
        "name": "knowledge_search",
        "description": (
            "Search the enterprise knowledge base (uploaded documents: manuals, "
            "specs, policies) with hybrid full-text + vector retrieval. Returns "
            "JSON {total, chunks:[{num, doc_id, doc_name, chunk_title, content, "
            "score}]} — cite chunks in answers as 【num】. Use this whenever a "
            "question may be answered by the customer's documents. Omit kb_id to "
            "search all knowledge bases (recommended); only pass kb_id when the "
            "user or system explicitly scopes to one base."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索问题或关键词（用用户的原始表述即可）。",
                },
                "kb_id": {
                    "type": "string",
                    "description": "可选：限定检索的知识库 id；省略则检索全部知识库。",
                },
                "topk": {
                    "type": "integer",
                    "description": "返回分块数（默认取部署配置，上限 20）。",
                },
            },
            "required": ["query"],
        },
    },
    handler=_handle,
    check_fn=_check_knowledge_enabled,
)
