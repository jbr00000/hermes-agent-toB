"""Upload routes: POST /uploads, GET /uploads, DELETE /uploads/{id}.

临时附件（chat 会话 / agent 任务共用）：用户把文件上传为问答上下文，
服务端解析全文（不分块）供注入模型。owner_type=session 挂在 chat 会话上，
owner_type=task 挂在 agent 任务上；随 owner 删除（DB 行级联在 repository，
磁盘清理由 sessions/tasks 的删除路由调用 uploads.remove_owner_files）。

写路径按 owner 类型做 feature 门控（session→chat、task→agent），读/删不
门控——与 sessions 路由同一原则：feature 被回收的用户仍能管理自己的数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from server import auth
from server import uploads as uploads_module
from server.deps import get_current_user
from server.knowledge.parser_client import SUPPORTED_EXTS
from server.storage import get_repository

router = APIRouter(prefix="/uploads", tags=["uploads"])

OwnerType = Literal["session", "task"]


def _owner_or_404(user: dict, owner_type: str, owner_id: str) -> None:
    """校验 owner 存在且属于当前用户；顺带做 feature 门控。"""
    repository = get_repository()
    if owner_type == "session":
        if not auth.user_features(user).get("chat", True):
            raise HTTPException(status_code=403, detail="feature 'chat' is disabled for this user")
        conversation = repository.get_conversation(owner_id)
        if conversation is None or conversation["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="session not found")
        if conversation["interaction_type"] != "chat":
            # agent 会话的附件挂在任务上（owner_type=task），别挂错层级
            raise HTTPException(status_code=400, detail="agent 会话的附件请挂到对应 task 上")
        return
    if not auth.user_features(user).get("agent", True):
        raise HTTPException(status_code=403, detail="feature 'agent' is disabled for this user")
    if repository.get_owned_task(user["id"], owner_id) is None:
        raise HTTPException(status_code=404, detail="task not found")


@router.post("", status_code=201)
async def upload_files(
    owner_type: OwnerType = Form(...),
    owner_id: str = Form(...),
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """上传 1~N 个附件（multipart）。每个 owner 累计最多 5 个、单文件 ≤20MB。

    落盘后立即返回（parse_status=parsing），解析在后台线程进行；前端轮询
    GET /uploads 刷新状态（parsing → ready | failed）。
    """
    _owner_or_404(user, owner_type, owner_id)
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")

    repository = get_repository()
    existing = repository.count_uploaded_files(owner_type, owner_id)
    if existing + len(files) > uploads_module.MAX_FILES_PER_OWNER:
        raise HTTPException(
            status_code=400,
            detail=f"每个{'会话' if owner_type == 'session' else '任务'}最多上传 "
            f"{uploads_module.MAX_FILES_PER_OWNER} 个文件（已有 {existing} 个）",
        )

    saved: list[dict] = []
    for file in files:
        file_name = Path(file.filename or "").name  # 剥掉任何路径成分
        file_ext = Path(file_name).suffix.lower()
        if file_ext not in SUPPORTED_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式 {file_ext or '(无扩展名)'}，支持 {sorted(SUPPORTED_EXTS)}",
            )
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"空文件: {file_name}")
        if len(content) > uploads_module.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小限制（{uploads_module.MAX_FILE_BYTES // 1024 // 1024}MB）: {file_name}",
            )
        saved.append(
            uploads_module.save_upload(user["id"], owner_type, owner_id, file_name, content)
        )
    if saved and owner_type == "task":
        # agent 任务的附件原件同时暂存进沙箱任务工作区 uploads/（execute 阶段
        # 模型可用终端直接读取原始格式）；上传时暂存一份，execute 前还会幂等补齐
        uploads_module.stage_task_uploads(user["id"], owner_id)
    return {"files": saved}


@router.get("")
def list_uploads(
    owner_type: OwnerType,
    owner_id: str,
    user: dict = Depends(get_current_user),
):
    """列出某 owner 的全部附件（含解析状态/token 数，供前端轮询刷新）。

    附带 token 预算用量：附件全文合计超过预算时 over_budget=true，前端
    显示黄色警告条（不阻断发送，超出部分从最新文件开始截断）。
    """
    files = get_repository().list_uploaded_files(user["id"], owner_type, owner_id)
    return {
        "files": files,
        "budget": uploads_module.budget_summary(user["id"], owner_type, owner_id),
    }


@router.delete("/{file_id}")
def delete_upload(file_id: str, user: dict = Depends(get_current_user)):
    record = get_repository().delete_uploaded_file(user["id"], file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    uploads_module.remove_file_files(record)
    return {"deleted": file_id}
