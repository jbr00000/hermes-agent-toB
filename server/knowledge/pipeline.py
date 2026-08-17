"""Knowledge build pipeline: parse → chunk → persist → sync (ES + Milvus).

状态机：uploaded → pending → parsing → syncing → ready / failed。
``uploaded``（已上传待解析）永远不会隐式进入本流水线——只有管理员显式选择
解析（retry/批量 parse 接口）才会把文档置为 pending 并入队。
MySQL 是事实源——chunk 先落 ``knowledge_chunks`` 表（``replace_knowledge_chunks``
原子替换），再把 ES/Milvus 当投影同步；任一步失败都可安全重试。
"""
from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.storage import get_repository

from .chunker import chunk_document
from .parser_client import parse_document
from .sync_service import synchronize_document

logger = logging.getLogger(__name__)


class DocumentNotPendingError(RuntimeError):
    """The document is not in ``pending`` — it was never explicitly queued for
    parsing (upload and parse are decoupled). Job fails, doc status untouched."""


def run_job(
    job: dict[str, Any],
    worker_id: str,
    *,
    config: KnowledgeDeploymentConfig | None = None,
) -> str:
    """Execute one knowledge job; returns ``"succeeded"`` or ``"failed"``."""
    job_id = str(job["job_id"])
    doc_id = str(job["doc_id"])
    repository = get_repository()
    cfg = config or load_deployment_config().knowledge
    now = time.time()
    repository.update_knowledge_job(
        job_id,
        status="running",
        worker_id=worker_id,
        started_at=now,
        heartbeat_at=now,
        error=None,
    )
    try:
        _run_pipeline(job, cfg, repository)
    except DocumentNotPendingError as exc:
        # 误入队的 job：只终结 job，不动文档状态（uploaded 不能被标 failed）。
        logger.warning("knowledge job %s skipped: %s", job_id, exc)
        repository.update_knowledge_job(
            job_id, status="failed", error=str(exc)[:2000], finished_at=time.time()
        )
        return "failed"
    except Exception as exc:
        logger.exception("knowledge job %s (doc %s) failed", job_id, doc_id)
        repository.update_knowledge_document(doc_id, status="failed", error=str(exc)[:2000])
        repository.update_knowledge_job(
            job_id, status="failed", error=str(exc)[:2000], finished_at=time.time()
        )
        return "failed"
    repository.update_knowledge_job(job_id, status="succeeded", finished_at=time.time())
    return "succeeded"


def _run_pipeline(
    job: dict[str, Any], cfg: KnowledgeDeploymentConfig, repository: Any
) -> None:
    doc_id = str(job["doc_id"])
    document = repository.get_knowledge_document(doc_id)
    if document is None:
        raise FileNotFoundError(f"知识库文档不存在（可能已被删除）: {doc_id}")
    if document["status"] != "pending":
        # 上传与解析已解耦：只有显式触发解析（置 pending 并入队）的文档才能
        # 进入流水线；uploaded/ready 等状态的入队属于误操作，直接拒绝。
        raise DocumentNotPendingError(
            f"文档状态为 {document['status']}，不在待解析队列中: {doc_id}"
        )

    repository.update_knowledge_document(doc_id, status="parsing", error=None)
    file_path = Path(document["file_path"])
    if not file_path.is_absolute():
        from hermes_constants import get_hermes_home

        file_path = get_hermes_home() / file_path
    # Office 文档的 LibreOffice 转换 PDF 留作前端预览件：<同 stem>.pdf，与原件同目录
    parsed = parse_document(
        file_path,
        str(document["file_ext"]),
        cfg,
        preview_dest=file_path.with_suffix(".pdf"),
    )
    embed_batch = None
    semantic_cfg = None
    if cfg.chunk_mode == "semantic":
        from dataclasses import asdict

        from .embedder import get_embedder
        from .semantic_chunker import SemanticChunkConfig

        embed_batch = get_embedder(cfg).embed
        semantic_cfg = SemanticChunkConfig(**asdict(cfg.semantic))
    chunks = chunk_document(
        parsed.content_list,
        chunk_size=cfg.chunk_size,
        chunk_overlap=cfg.chunk_overlap,
        mode=cfg.chunk_mode,
        embed_batch=embed_batch,
        semantic=semantic_cfg,
        min_tail_tokens=cfg.min_chunk_tokens,
    )
    if not chunks:
        raise ValueError(f"文档解析后没有任何可分块内容: {document['file_name']}")
    repository.replace_knowledge_chunks(
        doc_id,
        str(document["file_name"]),
        [
            {
                "chunk_title": chunk.chunk_title,
                "content": chunk.content,
                "doc_pos": chunk.doc_pos,
                "token_num": chunk.token_num,
            }
            for chunk in chunks
        ],
    )

    repository.update_knowledge_document(doc_id, status="syncing", parser=parsed.parser)
    synchronize_document(doc_id, config=cfg)

    repository.update_knowledge_document(
        doc_id,
        status="ready",
        chunk_count=len(chunks),
        finished_at=time.time(),
    )
    logger.info(
        "knowledge doc %s ready: %d chunks (parser=%s)", doc_id, len(chunks), parsed.parser
    )
