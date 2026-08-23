"""User-scoped Agent task lifecycle endpoints."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server import auth
from server import sessions as session_service
from server.agent_queue import enqueue_agent_run, stream_agent_run
from server.deps import get_current_user, require_feature
from server.storage import get_repository, get_runtime_store

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_feature("agent"))],
)


class CreateTaskRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    source_session_id: str | None = Field(default=None, min_length=1, max_length=64)


class KnowledgeScopeRequest(BaseModel):
    # 运行级知识库选择：整个 knowledge 字段缺省 = 保持现状（挂 knowledge
    # 工具集、全库可检索）；enabled=false 本轮不带知识库；kb_id 限定指定库
    enabled: bool = True
    kb_id: str | None = Field(default=None, min_length=1, max_length=64)


class PlanTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    knowledge: KnowledgeScopeRequest | None = None


class ExecuteTaskRequest(BaseModel):
    message: str | None = Field(default=None, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    knowledge: KnowledgeScopeRequest | None = None


class RetryTaskRequest(BaseModel):
    message: str | None = Field(default=None, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    knowledge: KnowledgeScopeRequest | None = None


class PermissionRequest(BaseModel):
    mode: Literal["read", "controlled", "full"]
    # None（默认）= 持久权限：一次切换长期生效，直到用户手动切回只读；
    # 传秒数则为定时租约（兼容旧客户端），到期自动回落只读
    ttl_seconds: int | None = Field(default=None, ge=60, le=3600)


class ToolApprovalDecisionRequest(BaseModel):
    decision: Literal["allow", "deny", "allow_all"]


def _resolve_knowledge_scope(
    user: dict, scope: KnowledgeScopeRequest | None
) -> dict | None:
    """校验并物化运行级知识库选择。必须在入队（任何存储副作用）之前调用。

    返回 None 表示缺省行为（knowledge 工具集照挂、全库可检索）；返回 dict
    则随 job 负载传给 worker 侧的 build_agent。
    """
    if scope is None:
        return None
    if not scope.enabled:
        if scope.kb_id:
            raise HTTPException(status_code=400, detail="kb_id 需要知识库检索开启")
        return {"enabled": False, "kb_id": None, "kb_name": None}
    if not auth.user_features(user).get("knowledge", True):
        raise HTTPException(
            status_code=403, detail="feature 'knowledge' is disabled for this user"
        )
    from server.deployment_config import load_deployment_config

    if not load_deployment_config().knowledge.enabled:
        raise HTTPException(status_code=409, detail="知识库未启用")
    kb_name = None
    if scope.kb_id:
        knowledge_base = get_repository().get_knowledge_base(scope.kb_id)
        if knowledge_base is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        kb_name = knowledge_base.get("name")
    return {"enabled": True, "kb_id": scope.kb_id, "kb_name": kb_name}


def _task_detail(user_id: str, task_id: str) -> dict:
    repository = get_repository()
    task = repository.get_owned_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    session = repository.get_owned_conversation(user_id, task["session_id"])
    active_run = repository.get_active_model_run(user_id, task["session_id"])
    if active_run is not None:
        snapshot = get_runtime_store().get_chat_snapshot(active_run["id"])
        if snapshot and snapshot.get("conversation_id") == task["session_id"]:
            active_run = {
                **active_run,
                "partial_content": snapshot.get("content") or "",
                "sequence": int(snapshot.get("sequence") or 0),
                "snapshot_updated_at": snapshot.get("updated_at"),
            }
    return {
        **task,
        "session": session,
        "messages": repository.get_messages(task["session_id"]),
        "active_run": active_run,
        "plan": repository.get_latest_task_plan(user_id, task_id),
        "permission": repository.get_task_permission(user_id, task_id),
        "runs": repository.list_task_runs(user_id, task_id),
        "events": repository.list_tool_events(user_id, task_id),
        "artifacts": repository.list_artifacts(user_id, task_id),
    }


@router.post("", status_code=201)
def create_task(req: CreateTaskRequest, user: dict = Depends(get_current_user)):
    try:
        task = get_repository().create_agent_task(
            user["id"], req.title, source_session_id=req.source_session_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source Chat session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task": _task_detail(user["id"], task["id"])}


@router.get("")
def list_tasks(user: dict = Depends(get_current_user)):
    return {"tasks": get_repository().list_tasks(user["id"])}


@router.get("/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    return {"task": _task_detail(user["id"], task_id)}


_ARTIFACTS_LIMIT = 200


def _task_artifacts_root(user_id: str, task_id: str) -> Path:
    """任务工作区（宿主机侧，绑定挂载进沙箱容器）的属主校验 + 路径解析。"""
    if get_repository().get_owned_task(user_id, task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    from server.sandbox import task_workspace_dir

    try:
        return task_workspace_dir(user_id, task_id, create=False)
    except ValueError:
        raise HTTPException(status_code=404, detail="task not found") from None


def _resolve_artifact_path(root: Path, raw_path: str) -> Path:
    """把客户端给的相对路径钉死在任务工作区内（防 ../ 逃逸与隐藏文件）。"""
    rel = PurePosixPath(raw_path)
    if (
        rel.is_absolute()
        or not rel.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in rel.parts)
    ):
        raise HTTPException(status_code=400, detail="invalid artifact path")
    base = root.resolve()
    target = (base / Path(rel)).resolve()
    if target == base or base not in target.parents:
        raise HTTPException(status_code=400, detail="invalid artifact path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return target


@router.get("/{task_id}/artifacts")
def list_task_artifacts(task_id: str, user: dict = Depends(get_current_user)):
    """列出任务工作区里的交付文件（沙箱内 cwd 的产物，递归、排除隐藏文件）。"""
    root = _task_artifacts_root(user["id"], task_id)
    artifacts: list[dict] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            # uploads/ 是用户上传的原始附件（输入，不是交付物），不进交付卡片
            if rel.parts and rel.parts[0] == "uploads":
                continue
            stat = path.stat()
            artifacts.append(
                {
                    "name": path.name,
                    "path": rel.as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "media_type": mimetypes.guess_type(path.name)[0],
                }
            )
            if len(artifacts) >= _ARTIFACTS_LIMIT:
                break
    return {"artifacts": artifacts}


@router.get("/{task_id}/artifacts/download")
def download_task_artifact(
    task_id: str,
    path: str = Query(min_length=1, max_length=512),
    user: dict = Depends(get_current_user),
):
    root = _task_artifacts_root(user["id"], task_id)
    target = _resolve_artifact_path(root, path)
    return FileResponse(
        target,
        filename=target.name,
        media_type=mimetypes.guess_type(target.name)[0]
        or "application/octet-stream",
    )


@router.delete("/{task_id}")
def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    result = repository.delete_owned_task(user["id"], task_id)
    if result == "missing":
        raise HTTPException(status_code=404, detail="task not found")
    if result == "running":
        raise HTTPException(status_code=409, detail="running task cannot be deleted")
    from server import uploads as uploads_module

    uploads_module.remove_owner_disk_files(user["id"], "task", task_id)
    if task is not None:
        # 任务删除会连带删掉其 conversation（delete_owned_task），附件目录一并清
        uploads_module.remove_owner_disk_files(user["id"], "session", task["session_id"])
    from server.sandbox import destroy_task_sandbox

    destroy_task_sandbox(user["id"], task_id)
    return {"deleted": task_id}


@router.put("/{task_id}/permission")
def update_permission(
    task_id: str,
    req: PermissionRequest,
    user: dict = Depends(get_current_user),
):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("current_run_id"):
        raise HTTPException(status_code=409, detail="permission cannot change during a run")
    try:
        permission = repository.set_task_permission(
            user["id"], task_id, req.mode, req.ttl_seconds
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.record_audit_event(
        event_type="task_permission",
        conversation_id=task["session_id"],
        user_id=user["id"],
        status="completed",
        mode=req.mode,
        metadata={"task_id": task_id, "expires_at": permission.get("expires_at")},
        error=None,
    )
    return {"task_id": task_id, "permission": permission}


@router.post("/{task_id}/plan")
async def plan_task(
    task_id: str,
    req: PlanTaskRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    task = get_repository().get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    request_id = req.request_id or str(uuid.uuid4())
    if task.get("current_run_id"):
        if task["current_run_id"] == request_id:
            return stream_agent_run(
                request,
                user_id=user["id"],
                task_id=task_id,
                request_id=request_id,
            )
        raise HTTPException(status_code=409, detail="task already has an active run")
    knowledge_scope = _resolve_knowledge_scope(user, req.knowledge)
    mode_state = session_service.resolve_chat_mode(user["id"], task["session_id"], "plan")
    enqueue_agent_run(
        user_id=user["id"],
        task=task,
        request_id=request_id,
        phase="plan",
        message=req.message,
        permission_mode="read",
        plan_state=mode_state["state"],
        knowledge=knowledge_scope,
    )
    return stream_agent_run(
        request,
        user_id=user["id"],
        task_id=task_id,
        request_id=request_id,
    )


@router.post("/{task_id}/approve")
def approve_task(task_id: str, user: dict = Depends(get_current_user)):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    plan = repository.approve_task_plan(user["id"], task_id)
    if plan is None:
        raise HTTPException(status_code=409, detail="task has no pending plan")
    repository.record_audit_event(
        event_type="task_plan_approval",
        conversation_id=task["session_id"],
        user_id=user["id"],
        status="completed",
        mode="plan",
        metadata={"task_id": task_id, "plan_id": plan["id"], "version": plan["version"]},
        error=None,
    )
    return {"task": _task_detail(user["id"], task_id)}


@router.post("/{task_id}/execute")
async def execute_task(
    task_id: str,
    req: ExecuteTaskRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    request_id = req.request_id or str(uuid.uuid4())
    if task.get("current_run_id"):
        if task["current_run_id"] == request_id:
            return stream_agent_run(
                request,
                user_id=user["id"],
                task_id=task_id,
                request_id=request_id,
            )
        raise HTTPException(status_code=409, detail="task already has an active run")
    plan = repository.get_latest_task_plan(user["id"], task_id)
    if plan is None or plan.get("status") != "approved":
        raise HTTPException(status_code=409, detail="approved plan required before execute")
    knowledge_scope = _resolve_knowledge_scope(user, req.knowledge)
    mode_state = session_service.enter_execute_mode(user["id"], task["session_id"])
    instruction = (req.message or "").strip()
    if instruction:
        # 追加指令（执行阶段的跟进消息）：会话历史已含已批准计划与此前结果，
        # 不再拼接计划全文；聊天流里显示用户原文而非"执行已批准计划"。
        message = instruction
        display_message: str | None = instruction
    else:
        message = (
            "Execute the approved plan exactly as approved."
            f"\n\nApproved plan:\n{plan['content']}"
        )
        display_message = None
    enqueue_agent_run(
        user_id=user["id"],
        task=task,
        request_id=request_id,
        phase="execute",
        message=message,
        display_message=display_message,
        permission_mode=repository.get_task_permission(user["id"], task_id)["mode"],
        plan_state=mode_state["state"],
        knowledge=knowledge_scope,
    )
    return stream_agent_run(
        request,
        user_id=user["id"],
        task_id=task_id,
        request_id=request_id,
    )


@router.post("/{task_id}/cancel", status_code=202)
def cancel_task(task_id: str, user: dict = Depends(get_current_user)):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    request_id = task.get("current_run_id")
    if not request_id:
        raise HTTPException(status_code=409, detail="task is not running")
    run = repository.get_owned_model_run(user["id"], request_id)
    if run is None or run["status"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="task is not running")
    if not repository.request_task_run_cancel(user["id"], request_id):
        raise HTTPException(status_code=409, detail="task is not running")
    get_runtime_store().cancel_request(request_id)
    return {"request_id": request_id, "status": "cancelling"}


@router.get("/{task_id}/runs/{request_id}/events")
def reconnect_task_run(
    task_id: str,
    request_id: str,
    request: Request,
    content_offset: int = Query(default=0, ge=0, le=10_000_000),
    user: dict = Depends(get_current_user),
):
    return stream_agent_run(
        request,
        user_id=user["id"],
        task_id=task_id,
        request_id=request_id,
        content_offset=content_offset,
    )


@router.get("/{task_id}/tool-approvals")
def list_tool_approvals(
    task_id: str,
    status: Literal["pending", "approved", "denied", "expired"] | None = Query(
        default=None
    ),
    user: dict = Depends(get_current_user),
):
    """controlled 档审批列表；前端重连时用它重建 pending 审批的 ground truth。"""
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "approvals": repository.list_tool_approvals(user["id"], task_id, status=status)
    }


@router.post("/{task_id}/tool-approvals/{approval_id}")
def decide_tool_approval(
    task_id: str,
    approval_id: str,
    req: ToolApprovalDecisionRequest,
    user: dict = Depends(get_current_user),
):
    """批准/拒绝一条待执行命令。不写 SSE——审批门控钩子的下一拍轮询会发现
    决定并经 executor 的 emit 发 tool.approval_resolved（单一 emitter 不撞号）。"""
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    approval = repository.get_tool_approval(approval_id)
    if (
        approval is None
        or approval["task_id"] != task_id
        or approval["user_id"] != user["id"]
    ):
        raise HTTPException(status_code=404, detail="approval not found")
    if approval["status"] != "pending":
        raise HTTPException(status_code=409, detail="approval already decided")
    decided = repository.decide_tool_approval(
        approval_id,
        user_id=user["id"],
        decision="denied" if req.decision == "deny" else "approved",
    )
    if decided is None:  # 并发决定竞态：另一请求已先落库
        raise HTTPException(status_code=409, detail="approval already decided")
    if req.decision == "allow_all":
        # 本次运行后续命令不再询问；Redis 部署下对 worker 进程可见。
        get_runtime_store().set_run_flag(approval["run_request_id"], "allow_all")
    return {"approval": decided}


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    req: RetryTaskRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    repository = get_repository()
    task = repository.get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    request_id = req.request_id or str(uuid.uuid4())
    if task.get("current_run_id"):
        if task["current_run_id"] == request_id:
            return stream_agent_run(
                request,
                user_id=user["id"],
                task_id=task_id,
                request_id=request_id,
            )
        raise HTTPException(status_code=409, detail="task already has an active run")
    runs = repository.list_task_runs(user["id"], task_id)
    if not runs:
        raise HTTPException(status_code=409, detail="task has no run to retry")
    knowledge_scope = _resolve_knowledge_scope(user, req.knowledge)

    phase = runs[-1]["phase"]
    messages = repository.get_messages(task["session_id"])
    previous_user_message = next(
        (message["content"] for message in reversed(messages) if message["role"] == "user"),
        None,
    )
    if phase == "plan":
        message = (req.message or previous_user_message or "Regenerate the task plan.").strip()
        mode = "plan"
    else:
        plan = repository.get_latest_task_plan(user["id"], task_id)
        if plan is None or plan.get("status") != "approved":
            raise HTTPException(status_code=409, detail="approved plan required before retry")
        mode_state = session_service.enter_execute_mode(user["id"], task["session_id"])
        instruction = (req.message or "Retry the approved plan from the failed step.").strip()
        message = f"{instruction}\n\nApproved plan:\n{plan['content']}"
        mode = "execute"
    if mode == "plan":
        mode_state = session_service.resolve_chat_mode(
            user["id"], task["session_id"], "plan"
        )
    permission_mode = (
        "read"
        if mode == "plan"
        else repository.get_task_permission(user["id"], task_id)["mode"]
    )
    enqueue_agent_run(
        user_id=user["id"],
        task=task,
        request_id=request_id,
        phase=mode,
        message=message,
        permission_mode=permission_mode,
        plan_state=mode_state["state"],
        knowledge=knowledge_scope,
    )
    return stream_agent_run(
        request,
        user_id=user["id"],
        task_id=task_id,
        request_id=request_id,
    )
