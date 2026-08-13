"""Knowledge-base routes: read-only for all users, admin-only mutations.

企业统一知识库（本期无个人库/无 RAG）：普通用户可浏览文档与分块；上传/删除/
重试仅 admin。``knowledge.enabled=false`` 时整组路由 404。

上传链路：multipart 落盘 ``$HERMES_HOME/knowledge/files/`` → document(pending)
+ job → 队列 → worker 异步构建（202 立即返回，前端轮询状态）。
"""
from __future__ import annotations

import logging
from pathlib import Path
import time
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from hermes_constants import get_hermes_home
from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.deps import get_current_user, require_admin
from server.knowledge.parser_client import SUPPORTED_EXTS
from server.knowledge.queue import enqueue_knowledge_job
from server.storage import get_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_read_router = APIRouter(dependencies=[Depends(get_current_user)])
_admin_router = APIRouter(dependencies=[Depends(require_admin)])

_FILES_DIR = Path("knowledge") / "files"


def _knowledge_config() -> KnowledgeDeploymentConfig:
    config = load_deployment_config().knowledge
    if not config.enabled:
        raise HTTPException(status_code=404, detail="知识库未启用")
    return config


def _document_or_404(doc_id: str) -> dict:
    document = get_repository().get_knowledge_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


# ------------------------------------------------------------------ 只读组


@_read_router.get("/documents")
def list_documents(
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    repository = get_repository()
    documents = repository.list_knowledge_documents(status=status, limit=limit, offset=offset)
    return {"documents": documents, "stats": repository.knowledge_stats()}


@_read_router.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    return {"document": _document_or_404(doc_id)}


@_read_router.get("/documents/{doc_id}/chunks")
def list_chunks(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    _document_or_404(doc_id)
    return {"chunks": get_repository().list_knowledge_chunks(doc_id)}


# ------------------------------------------------------------------ 管理组


@_admin_router.post("/documents", status_code=202)
async def upload_document(
    file: UploadFile,
    title: str | None = Form(default=None),
    config: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    file_name = Path(file.filename or "").name  # 剥掉任何路径成分
    file_ext = Path(file_name).suffix.lower()
    if file_ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 {file_ext or '(无扩展名)'}，支持 {sorted(SUPPORTED_EXTS)}",
        )
    content = await file.read()
    max_bytes = config.max_file_mb * 1024 * 1024
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"文件超过大小限制（{config.max_file_mb}MB）"
        )

    files_dir = get_hermes_home() / _FILES_DIR
    files_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{file_ext}"
    (files_dir / stored_name).write_bytes(content)

    repository = get_repository()
    document = repository.create_knowledge_document(
        uploader_id=user["id"],
        title=(title or "").strip() or Path(file_name).stem,
        file_name=file_name,
        file_ext=file_ext,
        size_bytes=len(content),
        file_path=str(_FILES_DIR / stored_name),
    )
    try:
        job = enqueue_knowledge_job(doc_id=document["id"], user_id=user["id"])
    except HTTPException:
        (files_dir / stored_name).unlink(missing_ok=True)
        repository.delete_knowledge_document(document["id"])
        raise
    repository.record_audit_event(
        event_type="knowledge_upload",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={
            "doc_id": document["id"],
            "file_name": file_name,
            "size_bytes": len(content),
        },
        error=None,
    )
    return {"document": document, "job_id": job["id"]}


@_admin_router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    document = _document_or_404(doc_id)
    repository = get_repository()

    # ES/Milvus 投影清理失败不阻断删除——MySQL 事实源删掉后投影是孤儿数据，
    # 记录日志即可（下次同 doc_id 不会复用，孤儿随索引重建消失）
    try:
        from server.knowledge.sync_service import clear_document

        clear_document(doc_id)
    except Exception:
        logger.warning("knowledge doc %s ES/Milvus 清理失败（继续删除 DB 记录）", doc_id)

    file_path = Path(document["file_path"])
    if not file_path.is_absolute():
        file_path = get_hermes_home() / file_path
    file_path.unlink(missing_ok=True)
    repository.delete_knowledge_document(doc_id)
    repository.record_audit_event(
        event_type="knowledge_delete",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"doc_id": doc_id, "file_name": document["file_name"]},
        error=None,
    )
    return {"deleted": doc_id}


@_admin_router.post("/documents/{doc_id}/retry", status_code=202)
def retry_document(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    document = _document_or_404(doc_id)
    if document["status"] not in {"failed", "ready"}:
        raise HTTPException(
            status_code=409, detail=f"文档正在构建中（{document['status']}），不能重试"
        )
    repository = get_repository()
    file_path = Path(document["file_path"])
    if not file_path.is_absolute():
        file_path = get_hermes_home() / file_path
    if not file_path.exists():
        raise HTTPException(status_code=410, detail="原始文件已丢失，请重新上传")

    repository.update_knowledge_document(
        doc_id,
        status="pending",
        error=None,
        retry_count=int(document.get("retry_count") or 0) + 1,
    )
    try:
        job = enqueue_knowledge_job(doc_id=doc_id, user_id=user["id"])
    except HTTPException:
        # 队列不可用 → 回滚状态，避免文档卡在 pending 无 job 可消费
        repository.update_knowledge_document(
            doc_id, status=document["status"], retry_count=document.get("retry_count") or 0
        )
        raise
    repository.record_audit_event(
        event_type="knowledge_retry",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"doc_id": doc_id},
        error=None,
    )
    return {"document_id": doc_id, "job_id": job["id"]}


router.include_router(_read_router)
router.include_router(_admin_router)
