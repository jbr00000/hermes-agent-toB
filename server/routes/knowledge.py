"""Knowledge-base routes: read-only for all users, admin-only mutations.

企业知识库三步流程（本期无个人库/无 RAG）：
  ① 新建知识库（``POST /knowledge/bases``，多库实体）
  ② 上传文档（``POST /knowledge/bases/{kb_id}/documents`` —— 只落盘 +
     document(uploaded)，**不入队**）
  ③ 选择文档解析（``POST /knowledge/documents/parse`` 批量入队，或单文档
     retry）
普通用户可浏览库/文档/分块（含未解析与失败文档）；所有变更仅 admin。
``knowledge.enabled=false`` 时整组路由 404。

解析链路：document(pending) + job → 队列 → worker 异步构建（202 立即返回，
前端轮询状态）。
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hermes_constants import get_hermes_home
from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config
from server.deps import get_current_user, require_admin, require_feature
from server.knowledge.chunker import count_tokens
from server.knowledge.parser_client import SUPPORTED_EXTS
from server.knowledge.queue import enqueue_knowledge_job
from server.storage import get_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 查看与变更都受 per-user「knowledge」功能开关控制（对 admin 同样生效），
# 与 memory/tasks 的整路由门控语义一致；变更仍额外要求 admin 角色。
_read_router = APIRouter(dependencies=[Depends(require_feature("knowledge"))])
_admin_router = APIRouter(
    dependencies=[Depends(require_admin), Depends(require_feature("knowledge"))]
)

_FILES_DIR = Path("knowledge") / "files"

# 可以被显式触发解析的状态：待解析的 uploaded + 失败重试的 failed。
_PARSEABLE_STATUSES = frozenset({"uploaded", "failed"})

# synchronize_document 的读-删-写不是原子的；同一文档的并发重同步（两次
# chunk 编辑撞车）可能让后写者用旧快照覆盖新内容。进程内 per-doc 锁串行化
# 即可（单进程部署；多进程/多实例的残余窗口接受，可用 resync 兜底修复）。
_SYNC_LOCKS: dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def _doc_sync_lock(doc_id: str) -> threading.Lock:
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(doc_id)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[doc_id] = lock
        return lock


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class ParseRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=200)


class KnowledgeChunkUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    chunk_title: str | None = Field(default=None, max_length=512)
    is_use: bool | None = None


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


def _base_or_404(kb_id: str) -> dict:
    base = get_repository().get_knowledge_base(kb_id)
    if base is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return base


def _abs_file_path(document: dict) -> Path:
    file_path = Path(document["file_path"])
    if not file_path.is_absolute():
        file_path = get_hermes_home() / file_path
    return file_path


def _enqueue_for_parse(document: dict, user: dict, *, count_retry: bool = False) -> str:
    """把一份 uploaded/failed 文档置 pending 并入队；队列不可用时回滚状态。

    返回 job_id。供批量 parse 与单文档 retry 共用；只有 retry 计 retry_count。
    """
    repository = get_repository()
    file_path = _abs_file_path(document)
    if not file_path.exists():
        raise HTTPException(status_code=410, detail="原始文件已丢失，请重新上传")

    previous_status = document["status"]
    previous_retry = int(document.get("retry_count") or 0)
    repository.update_knowledge_document(
        document["id"],
        status="pending",
        error=None,
        retry_count=previous_retry + 1 if count_retry else previous_retry,
    )
    try:
        job = enqueue_knowledge_job(doc_id=document["id"], user_id=user["id"])
    except HTTPException:
        # 队列不可用 → 回滚状态，避免文档卡在 pending 无 job 可消费
        repository.update_knowledge_document(
            document["id"], status=previous_status, retry_count=previous_retry
        )
        raise
    return str(job["id"])


def _cleanup_document_artifacts(document: dict) -> None:
    """清理一份文档的 ES/Milvus 投影与磁盘文件（best-effort，不阻断删除）。

    MySQL 事实源删掉后投影是孤儿数据，记录日志即可（下次同 doc_id 不会复用，
    孤儿随索引重建消失）。
    """
    try:
        from server.knowledge.sync_service import clear_document

        clear_document(document["id"])
    except Exception:
        logger.warning(
            "knowledge doc %s ES/Milvus 清理失败（继续删除 DB 记录）", document["id"]
        )
    _abs_file_path(document).unlink(missing_ok=True)


# ------------------------------------------------------------------ 只读组


@_read_router.get("/bases")
def list_bases(
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    return {"bases": get_repository().list_knowledge_bases()}


@_read_router.get("/documents")
def list_documents(
    kb_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    repository = get_repository()
    documents = repository.list_knowledge_documents(
        kb_id=kb_id, status=status, limit=limit, offset=offset
    )
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


_FILE_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


@_read_router.get("/documents/{doc_id}/file")
def get_document_file(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
):
    """原始上传文件（前端双栏视图的右侧"文档原始内容"用它做预览/下载）。"""
    document = _document_or_404(doc_id)
    path = _abs_file_path(document).resolve()
    if not path.exists():
        raise HTTPException(status_code=410, detail="原始文件已丢失，请重新上传")
    return FileResponse(
        path,
        media_type=_FILE_MEDIA_TYPES.get(document["file_ext"]),
        filename=document["file_name"],
        content_disposition_type="inline",
    )


# ------------------------------------------------------------------ 管理组：知识库


@_admin_router.post("/bases", status_code=201)
def create_base(
    body: KnowledgeBaseCreate,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    repository = get_repository()
    name = body.name.strip()
    if repository.get_knowledge_base_by_name(name) is not None:
        raise HTTPException(status_code=409, detail=f"知识库「{name}」已存在")
    base = repository.create_knowledge_base(
        name=name,
        creator_id=user["id"],
        description=(body.description or "").strip() or None,
    )
    repository.record_audit_event(
        event_type="knowledge_base_create",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"kb_id": base["id"], "name": base["name"]},
        error=None,
    )
    return {"base": base}


@_admin_router.patch("/bases/{kb_id}")
def update_base(
    kb_id: str,
    body: KnowledgeBaseUpdate,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    repository = get_repository()
    _base_or_404(kb_id)
    changes: dict[str, str | None] = {}
    if body.name is not None:
        name = body.name.strip()
        existing = repository.get_knowledge_base_by_name(name)
        if existing is not None and existing["id"] != kb_id:
            raise HTTPException(status_code=409, detail=f"知识库「{name}」已存在")
        changes["name"] = name
    if body.description is not None:
        changes["description"] = body.description.strip() or None
    if not changes:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    base = repository.update_knowledge_base(kb_id, **changes)
    repository.record_audit_event(
        event_type="knowledge_base_update",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"kb_id": kb_id, "changes": sorted(changes)},
        error=None,
    )
    return {"base": base}


@_admin_router.delete("/bases/{kb_id}")
def delete_base(
    kb_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    """级联删除：库内全部文档的 ES/Milvus 投影 + 磁盘文件 + DB 记录。"""
    repository = get_repository()
    _base_or_404(kb_id)
    deleted_docs = repository.delete_knowledge_base(kb_id)
    assert deleted_docs is not None  # _base_or_404 已确认存在
    for document in deleted_docs:
        _cleanup_document_artifacts(document)
    repository.record_audit_event(
        event_type="knowledge_base_delete",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"kb_id": kb_id, "documents": len(deleted_docs)},
        error=None,
    )
    return {"deleted": kb_id, "documents": len(deleted_docs)}


# ------------------------------------------------------------------ 管理组：文档


@_admin_router.post("/bases/{kb_id}/documents", status_code=202)
async def upload_document(
    kb_id: str,
    file: UploadFile,
    title: str | None = Form(default=None),
    config: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    """步骤②：上传文档。只落盘 + 建 uploaded 文档，**不入队解析**。"""
    _base_or_404(kb_id)
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
        kb_id=kb_id,
        uploader_id=user["id"],
        title=(title or "").strip() or Path(file_name).stem,
        file_name=file_name,
        file_ext=file_ext,
        size_bytes=len(content),
        file_path=str(_FILES_DIR / stored_name),
    )
    repository.record_audit_event(
        event_type="knowledge_upload",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={
            "doc_id": document["id"],
            "kb_id": kb_id,
            "file_name": file_name,
            "size_bytes": len(content),
        },
        error=None,
    )
    return {"document": document}


@_admin_router.post("/documents/parse", status_code=202)
def parse_documents(
    body: ParseRequest,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    """步骤③：把选中的 uploaded/failed 文档批量入队解析。

    构建中（pending/parsing/syncing）或已 ready 的文档跳过并说明原因。
    """
    repository = get_repository()
    queued: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for doc_id in dict.fromkeys(body.document_ids):  # 去重且保序
        document = repository.get_knowledge_document(doc_id)
        if document is None:
            skipped.append({"id": doc_id, "reason": "文档不存在"})
            continue
        if document["status"] not in _PARSEABLE_STATUSES:
            skipped.append(
                {"id": doc_id, "reason": f"当前状态（{document['status']}）不可解析"}
            )
            continue
        try:
            job_id = _enqueue_for_parse(document, user)
        except HTTPException as exc:
            skipped.append({"id": doc_id, "reason": str(exc.detail)})
            continue
        queued.append({"id": doc_id, "job_id": job_id})
    if queued:
        repository.record_audit_event(
            event_type="knowledge_parse",
            conversation_id=None,
            user_id=user["id"],
            status="completed",
            mode=None,
            metadata={"document_ids": [item["id"] for item in queued]},
            error=None,
        )
    return {"queued": queued, "skipped": skipped}


@_admin_router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    document = _document_or_404(doc_id)
    repository = get_repository()

    _cleanup_document_artifacts(document)
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
    job_id = _enqueue_for_parse(document, user, count_retry=True)
    get_repository().record_audit_event(
        event_type="knowledge_retry",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"doc_id": doc_id},
        error=None,
    )
    return {"document_id": doc_id, "job_id": job_id}


def _synchronize_locked(doc_id: str) -> int:
    """Run the doc-level ES/Milvus re-projection under the per-doc lock."""
    from server.knowledge.sync_service import synchronize_document

    with _doc_sync_lock(doc_id):
        return synchronize_document(doc_id)


@_admin_router.patch("/documents/{doc_id}/chunks/{chunk_id}")
def update_chunk(
    doc_id: str,
    chunk_id: str,
    body: KnowledgeChunkUpdate,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    """人工修正分块内容/标题或启停分块。

    MySQL 是事实源：先落库，再整文档重建 ES/Milvus 投影。投影失败（ES/
    Milvus/embedding 不可用）不让编辑失败——返回 synced=false，可用
    ``POST /documents/{doc_id}/resync`` 事后兜底。
    """
    repository = get_repository()
    document = _document_or_404(doc_id)
    if document["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"文档当前状态（{document['status']}）不可编辑分块",
        )
    chunk = repository.get_knowledge_chunk(chunk_id)
    if chunk is None or chunk["doc_id"] != doc_id:
        raise HTTPException(status_code=404, detail="chunk not found")
    if repository.get_active_knowledge_job(doc_id) is not None:
        # 重解析 job 会整文档删+重写 chunks，人工编辑会被无声覆盖
        raise HTTPException(status_code=409, detail="文档正在重建中，请稍后重试")

    fields = body.model_fields_set
    changes: dict[str, object] = {}
    if "content" in fields:
        if body.content is None or not body.content.strip():
            raise HTTPException(status_code=400, detail="content 不能为空")
        changes["content"] = body.content
        changes["token_num"] = count_tokens(body.content)
    if "chunk_title" in fields:
        changes["chunk_title"] = body.chunk_title  # None = 清除标题
    if body.is_use is not None:
        changes["is_use"] = body.is_use
    if not changes:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    updated = repository.update_knowledge_chunk(chunk_id, **changes)
    assert updated is not None  # 上面已确认存在

    synced = True
    sync_error: str | None = None
    try:
        _synchronize_locked(doc_id)
    except Exception as exc:
        synced = False
        sync_error = str(exc)[:500]
        logger.warning("chunk %s 已保存但索引同步失败（doc %s）: %s", chunk_id, doc_id, exc)
    repository.record_audit_event(
        event_type="knowledge_chunk_update",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "fields": sorted(k for k in changes if k != "token_num"),
            "synced": synced,
        },
        error=None,
    )
    return {"chunk": updated, "synced": synced, "sync_error": sync_error}


@_admin_router.post("/documents/{doc_id}/resync")
def resync_document(
    doc_id: str,
    _: KnowledgeDeploymentConfig = Depends(_knowledge_config),
    user: dict = Depends(get_current_user),
):
    """只重建 ES/Milvus 投影（不重解析、不动 chunks）——编辑同步失败的修复手段。"""
    repository = get_repository()
    document = _document_or_404(doc_id)
    if document["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"文档当前状态（{document['status']}）不可重新同步",
        )
    try:
        synced_chunks = _synchronize_locked(doc_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"索引同步失败: {str(exc)[:500]}"
        ) from exc
    repository.record_audit_event(
        event_type="knowledge_resync",
        conversation_id=None,
        user_id=user["id"],
        status="completed",
        mode=None,
        metadata={"doc_id": doc_id, "chunks": synced_chunks},
        error=None,
    )
    return {"doc_id": doc_id, "chunks": synced_chunks}


router.include_router(_read_router)
router.include_router(_admin_router)
