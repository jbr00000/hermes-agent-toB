"""问数元数据三端同步：MySQL（nl2sql_* 表，事实源）→ ES + Milvus 五索引。

仿 knowledge/sync_service.py 的幂等 delete-then-write：每次同步先按
``dataset_id`` 清掉两个引擎里该数据集的旧文档，再批量写入当前元数据——
重复执行/重试不会产生重复数据。五段资产（表结构/术语/指标/维度/范例）
各自独立成败，落进 ``nl2sql_search_sync_history``（图4「三端同步历史」）。

写入契约必须与 algorithm/retrieval.py + services.py 的读取契约一致：
  - 五索引名见 INDEX_NAMES（ES 索引与 Milvus collection 同名）
  - 向量字段名 "vector"，dim 取 deployment.yaml knowledge.embedding.dim
  - 每条文档含 ``id``（ES _id 与 Milvus 主键一致，RRF 融合靠它对齐）与
    ``dataset_id``（检索期过滤 + 同步期删除的作用域）
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Optional

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.knowledge import KnowledgeError
from server.knowledge.embedder import get_embedder
from server.knowledge.es_client import ElasticsearchClient, get_es_client
from server.knowledge.milvus_client import MilvusClient, escape_milvus_string, get_milvus_client
from server.nl2sql import store

from .algorithm.retrieval import VECTOR_FIELD
from .algorithm.services import INDEX_NAMES

logger = logging.getLogger(__name__)

SEGMENT_KEYS = ("ddl", "terminology", "index", "dimension", "qa_pair")

# Milvus VARCHAR 字段长度；文本写入前按 3 字节/字符留余量截断（同 knowledge 口径）
_MILVUS_MAX_CHARS = 1200


def _doc_id(*parts: str) -> str:
    """稳定文档 id（sha1 前 40 位）：ES _id 与 Milvus 主键共用，RRF 靠它跨引擎对齐。"""
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ DDL 字段切分

# MySQL 建表语句的列定义行：`col` type ... COMMENT '注释'（也容忍双引号/无注释）
_COLUMN_COMMENT_RE = re.compile(
    r"^\s*`?(?P<name>[A-Za-z_][\w$]*)`?\s+\w[^,]*?\bCOMMENT\s+(?P<q>['\"])(?P<comment>.*?)(?P=q)\s*[,)]?",
    re.IGNORECASE,
)
_DDL_SKIP_PREFIXES = ("primary", "key", "unique", "constraint", "index", "foreign", ")")


def _split_ddl_fields(dataset_id: str, table_name: str, ddl_content: str) -> list[dict[str, Any]]:
    """把一张表的 DDL 拆成字段级文档（只收带 COMMENT 的列——检索搜的就是 field_comment）。"""
    docs = []
    for line in (ddl_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith(_DDL_SKIP_PREFIXES):
            continue
        match = _COLUMN_COMMENT_RE.match(line)
        if not match:
            continue
        field_name = match.group("name")
        comment = match.group("comment").strip()
        if not comment:
            continue
        docs.append({
            "id": _doc_id(dataset_id, table_name, field_name),
            "dataset_id": dataset_id,
            "table_name": table_name,
            "field_name": field_name,
            "field_comment": comment,
            "_embed_text": comment,
        })
    return docs


# ------------------------------------------------------------------ 各段文档构建

def _build_ddl_docs(dataset_id: str, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for row in bundle["tables"]:
        if not row.get("enabled", True):
            continue  # 停用的表不下发（与算法端 select_schema 同口径）
        docs.extend(_split_ddl_fields(dataset_id, row["table_name"], row.get("ddl_content") or ""))
    return docs


def _build_terminology_docs(dataset_id: str, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    docs = []
    for row in bundle["terms"]:
        docs.append({
            "id": row["id"],
            "dataset_id": dataset_id,
            "terminology": row.get("terminology") or "",
            "synonyms": row.get("synonyms") or "",
            "terminology_explain": row.get("terminology_explain") or "",
            "_embed_text": " ".join(filter(None, [row.get("terminology"), row.get("synonyms") or ""])),
        })
    return docs


def _build_index_docs(dataset_id: str, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    docs = []
    for row in bundle["metrics"]:
        docs.append({
            "id": row["id"],
            "dataset_id": dataset_id,
            "index_name": row.get("index_name") or "",
            "index_display_name": row.get("index_display_name") or "",
            "calculate_method": row.get("calculate_method") or "",
            "_embed_text": (row.get("index_display_name") or row.get("index_name") or ""),
        })
    return docs


def _build_dimension_docs(dataset_id: str, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    docs = []
    for row in bundle["dimensions"]:
        docs.append({
            "id": row["id"],
            "dataset_id": dataset_id,
            "dimension_name": row.get("dimension_name") or "",
            "dimension_display_name": row.get("dimension_display_name") or "",
            "db_data_key": row.get("db_data_key") or "",
            "db_data_value": row.get("db_data_value") or "",
            "_embed_text": row.get("db_data_value") or "",
        })
    return docs


def _build_qa_pair_docs(dataset_id: str, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    docs = []
    for row in bundle["examples"]:
        docs.append({
            "id": row["id"],
            "dataset_id": dataset_id,
            "question": row.get("question") or "",
            "question_sql": row.get("question_sql") or "",
            "_embed_text": row.get("question") or "",
        })
    return docs


# 段定义：ES 文本字段（type=text，参与 multi_match）+ 其余标量字段（keyword）
_SEGMENT_DEFS: dict[str, dict[str, Any]] = {
    "ddl": {
        "index_key": "DDL_CHUNK",
        "build": _build_ddl_docs,
        "text_fields": ["field_comment"],
        "keyword_fields": ["table_name", "field_name"],
    },
    "terminology": {
        "index_key": "PROFESSIONAL_TERMINOLOGY",
        "build": _build_terminology_docs,
        "text_fields": ["terminology", "synonyms", "terminology_explain"],
        "keyword_fields": [],
    },
    "index": {
        "index_key": "INDEX",
        "build": _build_index_docs,
        "text_fields": ["index_display_name", "index_name"],
        "keyword_fields": ["calculate_method"],
    },
    "dimension": {
        "index_key": "DIMENSION",
        "build": _build_dimension_docs,
        "text_fields": ["db_data_value", "dimension_display_name"],
        "keyword_fields": ["dimension_name", "db_data_key"],
    },
    "qa_pair": {
        "index_key": "QA_PAIR",
        "build": _build_qa_pair_docs,
        "text_fields": ["question", "question_sql"],
        "keyword_fields": [],
    },
}


def _es_mappings(segment: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": {"type": "keyword"},
        "dataset_id": {"type": "keyword"},
    }
    for field in segment["text_fields"]:
        properties[field] = {"type": "text"}
    for field in segment["keyword_fields"]:
        properties[field] = {"type": "keyword"}
    return {"properties": properties}


def _milvus_fields(segment: dict[str, Any], dim: int) -> list[Any]:
    from pymilvus import DataType, FieldSchema

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
        FieldSchema(name="dataset_id", dtype=DataType.VARCHAR, max_length=64),
    ]
    for field in (*segment["text_fields"], *segment["keyword_fields"]):
        fields.append(FieldSchema(name=field, dtype=DataType.VARCHAR, max_length=4096))
    fields.append(FieldSchema(name=VECTOR_FIELD, dtype=DataType.FLOAT_VECTOR, dim=dim))
    return fields


def _milvus_delete_by_dataset(client: MilvusClient, collection_name: str, dataset_id: str) -> None:
    """按 dataset_id 清掉该数据集在 collection 里的全部行；collection 不存在为空操作。"""
    if not client.has_collection(collection_name):
        return
    collection = client.get_collection(collection_name)
    if hasattr(collection, "load"):
        collection.load()
    collection.delete(expr=f'dataset_id == "{escape_milvus_string(dataset_id)}"')
    collection.flush()


def _truncate(value: Any) -> str:
    return str(value or "")[:_MILVUS_MAX_CHARS]


def _sync_segment(
    segment_key: str,
    dataset_id: str,
    docs: list[dict[str, Any]],
    *,
    es_client: ElasticsearchClient,
    milvus_client: MilvusClient,
    embedder: Any,
    dim: int,
) -> str:
    """单段幂等同步 → 用户可读的条数消息。抛异常 = 该段失败（由调用方记 failed）。"""
    segment = _SEGMENT_DEFS[segment_key]
    index_name = INDEX_NAMES[segment["index_key"]]

    # 幂等：先建（缺时）再清后写；删除先于读快照已由 bundle 读取顺序保证
    es_client.create_index(index=index_name, mappings=_es_mappings(segment))
    es_client.delete_by_term(index_name, "dataset_id", dataset_id)
    milvus_client.create_collection(
        index_name,
        _milvus_fields(segment, dim),
        VECTOR_FIELD,
        {"index_type": "AUTOINDEX", "metric_type": "COSINE"},
    )
    _milvus_delete_by_dataset(milvus_client, index_name, dataset_id)

    docs = [doc for doc in docs if doc.get("_embed_text")]
    if not docs:
        return "0 条"

    vectors = embedder.embed([str(doc["_embed_text"]) for doc in docs])
    scalar_names = ["id", "dataset_id", *segment["text_fields"], *segment["keyword_fields"]]
    es_client.bulk_insert(
        index_name,
        [{field: _truncate(doc.get(field)) for field in scalar_names} for doc in docs],
        id_field="id",
    )
    milvus_client.batch_insert_data(
        index_name,
        [
            {
                **{field: _truncate(doc.get(field)) for field in scalar_names},
                VECTOR_FIELD: vector,
            }
            for doc, vector in zip(docs, vectors)
        ],
    )
    return f"{len(docs)} 条"


# ------------------------------------------------------------------ 入口

_SYNC_LOCK = threading.Lock()
_RUNNING: set[str] = set()  # 正在同步的 dataset_id（防重入）


def sync_dataset(
    dataset_id: str,
    *,
    trigger_type: str = "MANUAL_RESYNC",
    record_id: str | None = None,
    config: Optional[KnowledgeDeploymentConfig] = None,
) -> dict[str, Any]:
    """同步一个数据集的元数据到 ES + Milvus（同步执行；路由层走 start_sync 放后台）。

    返回落库后的同步历史记录。单段失败不影响其他段；任何段失败 → overall=failed。
    """
    cfg = config or load_deployment_config().knowledge
    if record_id is not None:
        record = store.get_sync_history(record_id)
        if record is None:
            raise KeyError(f"同步历史记录不存在: {record_id}")
    else:
        record = store.create_sync_history(dataset_id, trigger_type)

    bundle = store.get_meta_bundle(dataset_id)
    updates: dict[str, Any] = {}
    try:
        es_client = get_es_client(cfg)
        milvus_client = get_milvus_client(cfg)
        embedder = get_embedder(cfg)
        dim = cfg.embedding.dim
    except KnowledgeError as exc:
        for key in SEGMENT_KEYS:
            updates[f"{key}_status"] = "failed"
            updates[f"{key}_message"] = str(exc)[:200]
        updates["overall_status"] = "failed"
        updates["overall_message"] = f"检索端未就绪: {exc}"
        return store.update_sync_history(record["id"], updates)

    any_failed = False
    for key in SEGMENT_KEYS:
        try:
            docs = _SEGMENT_DEFS[key]["build"](dataset_id, bundle)
            message = _sync_segment(
                key, dataset_id, docs,
                es_client=es_client, milvus_client=milvus_client, embedder=embedder, dim=dim,
            )
            updates[f"{key}_status"] = "success"
            updates[f"{key}_message"] = message
            logger.info("问数同步 dataset=%s 段=%s: %s", dataset_id, key, message)
        except Exception as exc:
            any_failed = True
            updates[f"{key}_status"] = "failed"
            updates[f"{key}_message"] = str(exc)[:200]
            logger.warning("问数同步 dataset=%s 段=%s 失败: %s", dataset_id, key, exc, exc_info=True)

    updates["overall_status"] = "failed" if any_failed else "success"
    if any_failed:
        failed_keys = [key for key in SEGMENT_KEYS if updates.get(f"{key}_status") == "failed"]
        updates["overall_message"] = "部分资产同步失败：" + "、".join(failed_keys)
    return store.update_sync_history(record["id"], updates)


def start_sync(dataset_id: str, *, trigger_type: str = "MANUAL_RESYNC") -> dict[str, Any] | None:
    """请求线程内先落 running 记录（立即返回给前端），实际同步放后台线程。

    同数据集已有同步在执行时返回 None（防重入，前端据此提示「同步进行中」）。
    """
    with _SYNC_LOCK:
        if dataset_id in _RUNNING:
            return None
        _RUNNING.add(dataset_id)
    record = store.create_sync_history(dataset_id, trigger_type)

    def run() -> None:
        try:
            sync_dataset(dataset_id, trigger_type=trigger_type, record_id=record["id"])
        except Exception:
            logger.exception("问数同步 dataset=%s 未捕获异常", dataset_id)
        finally:
            with _SYNC_LOCK:
                _RUNNING.discard(dataset_id)

    threading.Thread(target=run, daemon=True, name=f"nl2sql-sync-{dataset_id[:8]}").start()
    return record
