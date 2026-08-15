"""Sync knowledge chunks from the MySQL source of truth into ES + Milvus.

MySQL（knowledge_chunks 表）是事实源；ES（BM25 全文）与 Milvus（向量）是
投影。写入采用幂等的 delete-then-write：先按 doc_id 清掉两个引擎里的旧数
据，再批量写入当前 chunk 集——重复执行/重试不会产生重复数据。
"""
from __future__ import annotations

import logging

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.storage import get_repository

from . import KnowledgeError
from .embedder import get_embedder
from .es_client import CHUNK_INDEX_NAME, chunk_index_mappings, get_es_client
from .milvus_client import (
    CHUNK_COLLECTION_NAME,
    chunk_collection_fields,
    get_milvus_client,
)

logger = logging.getLogger(__name__)

_MILVUS_CONTENT_MAX_CHARS = 4000  # VARCHAR(8192) 内留足余量（UTF-8 中文 3 字节/字）


class SyncError(KnowledgeError):
    """ES/Milvus/embedding 同步失败。"""


def synchronize_document(
    doc_id: str, *, config: KnowledgeDeploymentConfig | None = None
) -> int:
    """Project one document's chunks into ES + Milvus. Returns chunk count."""
    cfg = config or load_deployment_config().knowledge
    repository = get_repository()
    chunks = [c for c in repository.list_knowledge_chunks(doc_id) if c.get("is_use", True)]

    es_client = get_es_client(cfg)
    milvus_client = get_milvus_client(cfg)

    # 幂等：先清后写
    es_client.create_index(index=CHUNK_INDEX_NAME, mappings=chunk_index_mappings())
    es_client.delete_by_term(CHUNK_INDEX_NAME, "doc_id", doc_id)
    milvus_client.delete_by_doc_id(CHUNK_COLLECTION_NAME, doc_id)
    if not chunks:
        return 0

    try:
        vectors = get_embedder(cfg).embed([str(c["content"]) for c in chunks])
    except KnowledgeError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise SyncError(f"embedding 失败: {exc}") from exc

    es_client.bulk_insert(
        CHUNK_INDEX_NAME,
        [
            {
                "id": chunk["id"],
                "kb_id": chunk.get("kb_id") or "",
                "doc_id": doc_id,
                "doc_name": chunk.get("doc_name") or "",
                "doc_pos": chunk["doc_pos"],
                "chunk_title": chunk.get("chunk_title") or "",
                "chunk_content": chunk["content"],
                "token_num": chunk.get("token_num") or 0,
                "is_use": "true",
            }
            for chunk in chunks
        ],
        id_field="id",
    )

    milvus_client.create_collection(
        CHUNK_COLLECTION_NAME,
        chunk_collection_fields(cfg.embedding.dim),
        "vector",
        {"index_type": "AUTOINDEX", "metric_type": "COSINE"},
    )
    milvus_client.batch_insert_data(
        CHUNK_COLLECTION_NAME,
        [
            {
                "id": chunk["id"],
                "kb_id": chunk.get("kb_id") or "",
                "doc_id": doc_id,
                "chunk_title": (chunk.get("chunk_title") or "")[:512],
                "content": str(chunk["content"])[:_MILVUS_CONTENT_MAX_CHARS],
                "vector": vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ],
    )
    logger.info("knowledge doc %s synced: %d chunks → ES + Milvus", doc_id, len(chunks))
    return len(chunks)


def clear_document(doc_id: str, *, config: KnowledgeDeploymentConfig | None = None) -> None:
    """Remove one document from ES + Milvus（删除文档时调用）。"""
    cfg = config or load_deployment_config().knowledge
    get_es_client(cfg).delete_by_term(CHUNK_INDEX_NAME, "doc_id", doc_id)
    get_milvus_client(cfg).delete_by_doc_id(CHUNK_COLLECTION_NAME, doc_id)
