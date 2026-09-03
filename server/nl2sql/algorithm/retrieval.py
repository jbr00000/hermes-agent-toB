"""问数混合检索（ES + Milvus）—— 移植 lone-ai ``core/nlp/search.py`` 的 hybrid_search。

保持 lone-ai 消费方契约不变：返回 ``[{"score": float, "source": {...}, ...}]``
按 score 降序截 top_k，``source`` 含 ``id`` 与全部 output_fields。

客户端改接本仓 knowledge 基建（deployment.yaml ``knowledge:`` 段）：
  - ES：server.knowledge.get_es_client().search(index, body)（bool/multi_match + term 过滤）
  - Milvus：get_milvus_client().get_collection(name) 自行 search（knowledge 的
    search 是 chunk 专用，output_fields 固定，不适用问数的五类索引）
  - embedding：get_embedder().embed（维度对 deployment knowledge.embedding.dim 校验）
  - rerank：get_reranker().rerank；未配置 rerank 端点时 "rerank" 降级为 "RRF"

任一路后端失败/索引不存在都降级为空列表（lone-ai 同口径），不让检索故障
中断问数主链路。
"""
from __future__ import annotations

import logging
from typing import Any

from server.knowledge.embedder import get_embedder
from server.knowledge.es_client import get_es_client
from server.knowledge.milvus_client import escape_milvus_string, get_milvus_client
from server.knowledge.rerank_client import get_reranker, rerank_configured

logger = logging.getLogger(__name__)

# 问数五类索引的向量字段名（阶段5 sync.py 建 collection 时须一致）
VECTOR_FIELD = "vector"

_RRF_BLEND = 0.3  # lone-ai 固定：score = 0.3*rrf + 0.7*加权归一分


def _es_filters(expr_columns: dict[str, Any]) -> list[dict[str, Any]]:
    """expr_columns → ES filter 子句。list 值用 terms，标量用 term。"""
    filters: list[dict[str, Any]] = []
    for key, value in (expr_columns or {}).items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            filters.append({"terms": {key: [str(item) for item in value]}})
        else:
            filters.append({"term": {key: str(value)}})
    return filters


def _milvus_expr(expr_columns: dict[str, Any]) -> str | None:
    """expr_columns → Milvus 标量表达式（全部值转字符串并转义）。"""
    clauses: list[str] = []
    for key, value in (expr_columns or {}).items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            items = ", ".join(f'"{escape_milvus_string(str(item))}"' for item in value)
            clauses.append(f"{key} in [{items}]")
        else:
            clauses.append(f'{key} == "{escape_milvus_string(str(value))}"')
    return " and ".join(clauses) if clauses else None


def es_search(
    es_index_name: str,
    query: str,
    search_fields: list[str],
    output_fields: list[str],
    top_k: int,
    expr_columns: dict[str, Any],
) -> list[dict[str, Any]]:
    """ES BM25 检索；失败/索引不存在 → []。"""
    try:
        client = get_es_client()
        body: dict[str, Any] = {
            "size": top_k,
            "_source": output_fields,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": search_fields,
                                "type": "best_fields",
                            }
                        }
                    ],
                }
            },
        }
        filters = _es_filters(expr_columns)
        if filters:
            body["query"]["bool"]["filter"] = filters
        payload = client.search(index=es_index_name, body=body)
        hits = payload.get("hits", {}).get("hits", [])
        return [
            {
                "source": {
                    **{field: hit.get("_source", {}).get(field, "") for field in output_fields},
                    "id": str(hit.get("_source", {}).get("id") or hit.get("_id") or ""),
                },
                "raw_score": float(hit.get("_score") or 0.0),
                "type": "es",
            }
            for hit in hits
        ]
    except Exception as exc:
        logger.warning("问数 ES 检索失败(index=%s): %s", es_index_name, exc)
        return []


def milvus_search(
    query: str,
    collection_name: str,
    output_fields: list[str],
    top_k: int,
    expr_columns: dict[str, Any],
) -> list[dict[str, Any]]:
    """Milvus 向量检索（COSINE）；collection 不存在/失败 → []。"""
    try:
        client = get_milvus_client()
        if not client.has_collection(collection_name):
            return []
        query_vector = get_embedder().embed([query])[0]
        result = client.get_collection(collection_name).search(
            data=[query_vector],
            anns_field=VECTOR_FIELD,
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            expr=_milvus_expr(expr_columns),
            output_fields=output_fields,
        )
        hits = []
        for hit in result[0]:
            source = {field: hit.entity.get(field) for field in output_fields}
            source["id"] = str(hit.id)
            hits.append(
                {
                    "source": source,
                    "raw_score": hit.distance if hit.distance is not None else 0.0,
                    "type": "vector",
                }
            )
        return hits
    except Exception as exc:
        logger.warning("问数 Milvus 检索失败(collection=%s): %s", collection_name, exc)
        return []


def normalize_scores(scores: list[float]) -> list[float]:
    """Min-Max 归一化；所有分数相同时返回均匀分布。"""
    if not scores:
        return []
    min_score = min(scores)
    max_score = max(scores)
    if max_score == min_score:
        return [1.0] * len(scores)
    return [(score - min_score) / (max_score - min_score) for score in scores]


def _apply_weighted_fusion(
    rrf_results: dict[Any, dict], weight_es: float, weight_vector: float
) -> dict[Any, dict]:
    """RRF 之后再叠加原始分的 min-max 归一加权融合（lone-ai 原样）。"""
    if not rrf_results:
        return {}
    total_weight = weight_es + weight_vector
    if total_weight != 1.0:
        weight_es = weight_es / total_weight
        weight_vector = weight_vector / total_weight

    uuids: list[Any] = []
    es_scores: list[float] = []
    vector_scores: list[float] = []
    for uuid, data in rrf_results.items():
        uuids.append(uuid)
        es_scores.append(data["raw_scores"].get("es") or 0)
        vector_scores.append(data["raw_scores"].get("vector") or 0)

    if es_scores and any(score > 0 for score in es_scores):
        normalized_es = normalize_scores(es_scores)
    else:
        normalized_es = [0.0] * len(es_scores)
    if vector_scores and any(score > 0 for score in vector_scores):
        normalized_vector = normalize_scores(vector_scores)
    else:
        normalized_vector = [0.0] * len(vector_scores)

    for i, uuid in enumerate(uuids):
        weighted_score = weight_es * normalized_es[i] + weight_vector * normalized_vector[i]
        rrf_results[uuid]["score"] = (
            _RRF_BLEND * rrf_results[uuid]["score"] + (1 - _RRF_BLEND) * weighted_score
        )
        rrf_results[uuid]["normalized_scores"] = {
            "es": normalized_es[i],
            "vector": normalized_vector[i],
            "weighted": weighted_score,
        }
    return rrf_results


def hybrid_search(
    query: str,
    collection_name: str,
    es_index_name: str,
    expr_columns: dict[str, Any],
    search_fields: list[str],
    output_fields: list[str],
    top_k: int = 5,
    rerank_way: str = "RRF",
    rerank_columns: list[str] | None = None,
    rrf_k: int = 60,
    weight_es: float = 0.5,
    weight_vector: float = 0.5,
) -> list[dict[str, Any]]:
    """混合检索（ES + Milvus），RRF / rerank 融合，按 score 降序截 top_k。

    相对 lone-ai 的裁剪：去掉 search_way（问数恒走 hybrid）、default_value
    （缺字段统一默认 ""）、weighted 模式（问数消费方未用）。
    """
    if not search_fields or not all(isinstance(field, str) for field in search_fields):
        logger.warning("问数检索 search_fields 非法: %r", search_fields)
        return []
    if not isinstance(top_k, int) or top_k <= 0:
        top_k = 5

    es_hits = es_search(es_index_name, query, search_fields, output_fields, top_k, expr_columns)
    milvus_hits = milvus_search(query, collection_name, output_fields, top_k, expr_columns)
    if not es_hits and not milvus_hits:
        return []

    if rerank_way == "rerank" and not rerank_configured():
        logger.info("rerank 端点未配置，问数检索降级为 RRF 融合")
        rerank_way = "RRF"

    rrf_results: dict[str, dict[str, Any]] = {}

    if rerank_way == "rerank":
        # 精排：rerank_columns 拼内容送 rerank 端点，得分直接作 score
        columns = rerank_columns or []
        uuids: list[str] = []
        contents: list[str] = []
        es_scores: list[float] = []
        milvus_scores: list[float] = []
        sources: list[dict[str, Any]] = []
        for hit in es_hits:
            uuid = hit["source"].get("id")
            if not uuid or uuid in uuids:
                continue
            uuids.append(uuid)
            contents.append("-".join(str(hit["source"].get(e) or "") for e in columns))
            es_scores.append(hit["raw_score"])
            milvus_scores.append(0.0)
            sources.append(hit["source"])
        for hit in milvus_hits:
            uuid = hit["source"].get("id")
            if not uuid:
                continue
            if uuid not in uuids:
                uuids.append(uuid)
                contents.append("-".join(str(hit["source"].get(e) or "") for e in columns))
                sources.append(hit["source"])
                milvus_scores.append(hit["raw_score"])
                es_scores.append(0.0)
            else:
                milvus_scores[uuids.index(uuid)] = hit["raw_score"]
        try:
            rerank_result = get_reranker().rerank(query, contents, top_n=len(contents))
        except Exception as exc:
            logger.warning("问数 rerank 失败，本轮按 0 分处理: %s", exc)
            rerank_result = [0.0] * len(uuids)
        for i, uuid in enumerate(uuids):
            rrf_results[uuid] = {
                "source": sources[i],
                "score": rerank_result[i],
                "raw_scores": {"es": es_scores[i], "vector": milvus_scores[i]},
            }
    else:
        # RRF：贡献值 = 1 / (rrf_k + 排名)，按 source["id"] 合并累加
        for rank, hit in enumerate(es_hits, start=1):
            uuid = hit["source"].get("id")
            if not uuid:
                continue
            rrf_results[uuid] = {
                "source": hit["source"],
                "score": 1.0 / (rrf_k + rank),
                "types": [hit["type"]],
                "raw_scores": {"es": hit["raw_score"], "vector": None},
            }
        for rank, hit in enumerate(milvus_hits, start=1):
            uuid = hit["source"].get("id")
            if not uuid:
                continue
            contribution = 1.0 / (rrf_k + rank)
            if uuid in rrf_results:
                rrf_results[uuid]["score"] += contribution
                rrf_results[uuid]["types"].append(hit["type"])
                rrf_results[uuid]["raw_scores"]["vector"] = hit["raw_score"]
            else:
                rrf_results[uuid] = {
                    "source": hit["source"],
                    "score": contribution,
                    "types": [hit["type"]],
                    "raw_scores": {"es": None, "vector": hit["raw_score"]},
                }
        rrf_results = _apply_weighted_fusion(rrf_results, weight_es, weight_vector)

    if not rrf_results:
        return []
    return sorted(rrf_results.values(), key=lambda item: item["score"], reverse=True)[:top_k]
