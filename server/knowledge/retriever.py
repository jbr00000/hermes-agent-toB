"""Hybrid retrieval over knowledge chunks: ES BM25 + Milvus vector → MySQL hydration.

两路召回（ES 全文 / Milvus 向量）按 lone-ai ``core/nlp/search.py`` 的策略融合：
RRF（k 可配）拿排名分，两路原始分各自 min-max 归一化后按权重加权，
``final = 0.3*RRF + 0.7*加权``。融合只产出 chunk id 顺序，正文一律从 MySQL
回表（事实源）——Milvus content 有 4000 字符截断、ES 是投影，且回表能复核
``is_use``（刚停用的块投影里可能还留着）。

单路失败降级为另一路；两路都失败抛 :class:`RetrievalError`，由工具层兜底。
"""
from __future__ import annotations

import logging
from typing import Any

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.storage import get_repository

from . import KnowledgeError
from .embedder import get_embedder
from .es_client import CHUNK_INDEX_NAME, get_es_client
from .milvus_client import CHUNK_COLLECTION_NAME, get_milvus_client

logger = logging.getLogger(__name__)

_RRF_BLEND = 0.3  # final = _RRF_BLEND*rrf + (1-_RRF_BLEND)*加权归一分


class RetrievalError(KnowledgeError):
    """ES 与 Milvus 两路检索均失败。"""


def search_chunks(
    query: str,
    *,
    kb_id: str | None = None,
    topk: int | None = None,
    config: KnowledgeDeploymentConfig | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``topk`` chunks (fused order), hydrated from MySQL.

    Each item: ``{chunk_id, doc_id, doc_name, kb_id, chunk_title, content,
    doc_pos, score}``. Empty list means "no relevant content"（不是错误）。
    """
    text = str(query or "").strip()
    if not text:
        return []
    cfg = config or load_deployment_config().knowledge
    tuning = cfg.retrieval
    limit = topk if topk and topk > 0 else tuning.topk

    es_hits = _es_search(text, kb_id=kb_id, size=tuning.es_candidates, config=cfg)
    vector_hits = _vector_search(text, kb_id=kb_id, size=tuning.vector_candidates, config=cfg)
    if es_hits is None and vector_hits is None:
        raise RetrievalError("知识库检索失败：ES 与 Milvus 均不可用")

    fused = _fuse(
        es_hits or [],
        vector_hits or [],
        rrf_k=tuning.rrf_k,
        vector_weight=tuning.vector_weight,
    )
    ordered_ids = [chunk_id for chunk_id, _ in fused][:limit]
    if not ordered_ids:
        return []

    score_by_id = dict(fused)
    results: list[dict[str, Any]] = []
    for row in get_repository().get_knowledge_chunks_by_ids(ordered_ids):
        if not row.get("is_use", True):
            continue  # 投影滞后：MySQL 里已停用的块不下发
        results.append(
            {
                "chunk_id": row["id"],
                "doc_id": row["doc_id"],
                "doc_name": row.get("doc_name") or "",
                "kb_id": row.get("kb_id") or "",
                "chunk_title": row.get("chunk_title") or "",
                "content": row["content"],
                "doc_pos": row["doc_pos"],
                "score": round(score_by_id.get(row["id"], 0.0), 6),
            }
        )
    return results


def _es_search(
    query: str, *, kb_id: str | None, size: int, config: KnowledgeDeploymentConfig
) -> list[dict[str, Any]] | None:
    """BM25 via the raw search endpoint. None = backend failed (降级另一路)."""
    try:
        client = get_es_client(config)
        filters: list[dict[str, Any]] = [{"term": {"is_use": "true"}}]
        if kb_id:
            filters.append({"term": {"kb_id": kb_id}})
        body = {
            "size": size,
            "_source": ["id"],
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["chunk_title^2", "chunk_content", "doc_name"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        payload = client.search(index=CHUNK_INDEX_NAME, body=body)
        hits = payload.get("hits", {}).get("hits", [])
        return [
            {"id": str(hit.get("_id") or ""), "score": float(hit.get("_score") or 0.0)}
            for hit in hits
            if hit.get("_id")
        ]
    except Exception as exc:
        logger.warning("knowledge ES search failed: %s", exc)
        return None


def _vector_search(
    query: str, *, kb_id: str | None, size: int, config: KnowledgeDeploymentConfig
) -> list[dict[str, Any]] | None:
    """Embedding + Milvus COSINE. None = backend failed（降级另一路）。"""
    try:
        vector = get_embedder(config).embed([query])[0]
        client = get_milvus_client(config)
        return [
            {"id": hit["id"], "score": hit["score"]}
            for hit in client.search(CHUNK_COLLECTION_NAME, vector, topk=size, kb_id=kb_id)
        ]
    except Exception as exc:
        logger.warning("knowledge vector search failed: %s", exc)
        return None


def _fuse(
    es_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    rrf_k: int,
    vector_weight: float,
) -> list[tuple[str, float]]:
    """RRF + min-max 加权融合，返回按 final 分降序的 [(chunk_id, score)]。"""
    merged: dict[str, dict[str, float]] = {}
    for rank, hit in enumerate(es_hits, start=1):
        entry = merged.setdefault(hit["id"], {"rrf": 0.0, "es": 0.0, "vector": 0.0})
        entry["rrf"] += 1.0 / (rrf_k + rank)
        entry["es"] = max(entry["es"], float(hit["score"]))
    for rank, hit in enumerate(vector_hits, start=1):
        entry = merged.setdefault(hit["id"], {"rrf": 0.0, "es": 0.0, "vector": 0.0})
        entry["rrf"] += 1.0 / (rrf_k + rank)
        entry["vector"] = max(entry["vector"], float(hit["score"]))
    if not merged:
        return []

    es_norm = _normalize([entry["es"] for entry in merged.values()])
    vector_norm = _normalize([entry["vector"] for entry in merged.values()])
    scored: list[tuple[str, float]] = []
    for (chunk_id, entry), es_n, vector_n in zip(merged.items(), es_norm, vector_norm):
        weighted = (1.0 - vector_weight) * es_n + vector_weight * vector_n
        scored.append((chunk_id, _RRF_BLEND * entry["rrf"] + (1.0 - _RRF_BLEND) * weighted))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _normalize(scores: list[float]) -> list[float]:
    """Min-max 归一化；整路缺失（全 0）时保持全 0，不给缺路结果白送分。"""
    if not scores or not any(score > 0 for score in scores):
        return [0.0] * len(scores)
    low, high = min(scores), max(scores)
    if high == low:
        return [1.0] * len(scores)
    return [(score - low) / (high - low) for score in scores]
