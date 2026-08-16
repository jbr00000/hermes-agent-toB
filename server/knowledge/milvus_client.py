"""Milvus client for knowledge chunks (vector projection).

Adapted from the reference project's ``infra/milvus.py``: a thin pymilvus
wrapper with a cached connection. pymilvus is imported lazily so the module
stays importable in environments that only run the API tier (tests stub the
``pymilvus`` module instead of connecting to a real server).

Single-tenant to-B deployment → the default Milvus database is enough; the
chunk collection is created lazily with the vector dim of the first batch.
"""
from __future__ import annotations

from threading import RLock
from typing import Any
import urllib.parse

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config

from . import KnowledgeDisabledError, KnowledgeError

CHUNK_COLLECTION_NAME = "knowledge_chunks"
_ALIAS = "knowledge_default"


def _pymilvus():
    try:
        import pymilvus
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject
        raise KnowledgeError("pymilvus 未安装，无法写入 Milvus") from exc
    return pymilvus


class MilvusClient:
    """pymilvus adapter: lazy collection creation + batched insert."""

    def __init__(self, *, uri: str, alias: str = _ALIAS):
        self.alias = alias
        parsed = urllib.parse.urlsplit(uri if "://" in uri else f"http://{uri}")
        pymilvus = _pymilvus()
        pymilvus.connections.connect(
            alias=self.alias,
            host=parsed.hostname or "localhost",
            port=str(parsed.port or 19530),
        )

    def has_collection(self, collection_name: str) -> bool:
        return bool(_pymilvus().utility.has_collection(collection_name, using=self.alias))

    def create_collection(
        self,
        collection_name: str,
        fields: list[Any],
        field_names: str | list[str],
        index_params: dict | list[dict],
    ) -> None:
        """Create the collection and its index; no-op when it already exists."""
        pymilvus = _pymilvus()
        if self.has_collection(collection_name):
            return
        schema = pymilvus.CollectionSchema(
            fields, description=f"Collection {collection_name}", enable_dynamic_field=True
        )
        collection = pymilvus.Collection(collection_name, schema, using=self.alias)
        field_list = [field_names] if isinstance(field_names, str) else field_names
        params_list = [index_params] if isinstance(index_params, dict) else index_params
        for field_name, params in zip(field_list, params_list):
            collection.create_index(field_name=field_name, index_params=params)

    def get_collection(self, collection_name: str) -> Any:
        return _pymilvus().Collection(collection_name, using=self.alias)

    def batch_insert_data(
        self, collection_name: str, data: list[dict[str, Any]], batch_size: int = 1000
    ) -> int:
        """Insert rows in batches; returns the number of inserted records."""
        if not data:
            return 0
        total = 0
        collection = self.get_collection(collection_name)
        for index in range(0, len(data), batch_size):
            batch = data[index : index + batch_size]
            result = collection.insert(batch)
            total += len(result.primary_keys)
        collection.flush()
        return total

    def delete_by_doc_id(self, collection_name: str, doc_id: str) -> None:
        """Delete all rows of one document; missing collection is a no-op."""
        if not self.has_collection(collection_name):
            return
        collection = self.get_collection(collection_name)
        if hasattr(collection, "load"):
            collection.load()
        collection.delete(expr=f'doc_id == "{escape_milvus_string(doc_id)}"')
        collection.flush()

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        *,
        topk: int,
        kb_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """COSINE ANN search; returns [{id, doc_id, chunk_title, score}] (score 越大越相似).

        Missing collection is treated as empty (nothing synced yet).
        """
        if not self.has_collection(collection_name):
            return []
        collection = self.get_collection(collection_name)
        collection.load()
        expr = f'kb_id == "{escape_milvus_string(kb_id)}"' if kb_id else None
        results = collection.search(
            data=[list(query_vector)],
            anns_field="vector",
            param={"metric_type": "COSINE"},
            limit=topk,
            output_fields=["id", "doc_id", "chunk_title"],
            expr=expr,
        )
        hits: list[dict[str, Any]] = []
        for hit in results[0] if results else []:
            entity = getattr(hit, "entity", None)
            hits.append(
                {
                    "id": str(hit.id),
                    "doc_id": str(entity.get("doc_id") or "") if entity is not None else "",
                    "chunk_title": str(entity.get("chunk_title") or "") if entity is not None else "",
                    "score": float(hit.distance or 0.0),
                }
            )
        return hits

    def drop_collection(self, collection_name: str) -> None:
        pymilvus = _pymilvus()
        if self.has_collection(collection_name):
            pymilvus.utility.drop_collection(collection_name, using=self.alias)


def escape_milvus_string(value: str) -> str:
    """Escape a string literal for use inside a Milvus filter expression."""
    return (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace('"', '\\"')
    )


def chunk_collection_fields(dim: int) -> list[Any]:
    """Field schema for the knowledge_chunks collection (vector dim from config)."""
    pymilvus = _pymilvus()
    FieldSchema, DataType = pymilvus.FieldSchema, pymilvus.DataType
    return [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
        FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chunk_title", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]


_CLIENT_LOCK = RLock()
_CLIENT: MilvusClient | None = None
_CLIENT_KEY: str | None = None


def get_milvus_client(config: KnowledgeDeploymentConfig | None = None) -> MilvusClient:
    """Return the cached Milvus client for the current deployment config."""
    global _CLIENT, _CLIENT_KEY
    cfg = config or load_deployment_config().knowledge
    if not cfg.enabled:
        raise KnowledgeDisabledError("knowledge.enabled=false，知识库未启用")
    if not cfg.milvus_uri:
        raise KnowledgeDisabledError("knowledge.milvus_uri 未配置")
    if _CLIENT is not None and _CLIENT_KEY == cfg.milvus_uri:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != cfg.milvus_uri:
            if _CLIENT is not None:
                _pymilvus().connections.disconnect(_CLIENT.alias)
            _CLIENT = MilvusClient(uri=cfg.milvus_uri)
            _CLIENT_KEY = cfg.milvus_uri
        return _CLIENT


def reset_milvus_client_for_tests() -> None:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _pymilvus().connections.disconnect(_CLIENT.alias)
        _CLIENT = None
        _CLIENT_KEY = None
