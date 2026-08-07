"""User-scoped Agent task lifecycle endpoints."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server import sessions as session_service
from server.deps import get_current_user
from server.routes import chat as chat_routes
from server.storage import get_repository, get_runtime_store

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)


class PlanTaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class ExecuteTaskRequest(BaseModel):
    message: str | None = Field(default=None, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class RetryTaskRequest(BaseModel):
    message: str | None = Field(default=None, max_length=100_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class PermissionRequest(BaseModel):
    mode: Literal["read", "controlled", "full"]
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


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
    task = get_repository().create_agent_task(user["id"], req.title)
    return {"task": _task_detail(user["id"], task["id"])}


@router.get("")
def list_tasks(user: dict = Depends(get_current_user)):
    return {"tasks": get_repository().list_tasks(user["id"])}


@router.get("/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    return {"task": _task_detail(user["id"], task_id)}


@router.delete("/{task_id}")
def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    result = get_repository().delete_owned_task(user["id"], task_id)
    if result == "missing":
        raise HTTPException(status_code=404, detail="task not found")
    if result == "running":
        raise HTTPException(status_code=409, detail="running task cannot be deleted")
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
    permission = repository.set_task_permission(
        user["id"], task_id, req.mode, req.ttl_seconds
    )
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
    if task.get("current_run_id"):
        raise HTTPException(status_code=409, detail="task already has an active run")
    return await chat_routes.chat(
        chat_routes.ChatRequest(
            message=req.message,
            session_id=task["session_id"],
            request_id=req.request_id or str(uuid.uuid4()),
            interaction_type="agent",
            mode="plan",
        ),
        request,
        user,
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
    if task.get("current_run_id"):
        raise HTTPException(status_code=409, detail="task already has an active run")
    plan = repository.get_latest_task_plan(user["id"], task_id)
    if plan is None or plan.get("status") != "approved":
        raise HTTPException(status_code=409, detail="approved plan required before execute")
    session_service.enter_execute_mode(user["id"], task["session_id"])
    instruction = (req.message or "Execute the approved plan exactly as approved.").strip()
    message = f"{instruction}\n\nApproved plan:\n{plan['content']}"
    return await chat_routes.chat(
        chat_routes.ChatRequest(
            message=message,
            session_id=task["session_id"],
            request_id=req.request_id or str(uuid.uuid4()),
            interaction_type="agent",
            mode="execute",
        ),
        request,
        user,
    )


@router.post("/{task_id}/cancel", status_code=202)
def cancel_task(task_id: str, user: dict = Depends(get_current_user)):
    task = get_repository().get_owned_task(user["id"], task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    request_id = task.get("current_run_id")
    if not request_id:
        raise HTTPException(status_code=409, detail="task is not running")
    return chat_routes.cancel_chat(request_id, user)


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
    if task.get("current_run_id"):
        raise HTTPException(status_code=409, detail="task already has an active run")
    runs = repository.list_task_runs(user["id"], task_id)
    if not runs:
        raise HTTPException(status_code=409, detail="task has no run to retry")

    phase = runs[-1]["phase"]
    messages = repository.get_messages(task["session_id"])
    previous_user_message = next(
        (message["content"] for message in reversed(messages) if message["role"] == "user"),
        None,
    )
    request_id = req.request_id or str(uuid.uuid4())
    if phase == "plan":
        message = (req.message or previous_user_message or "Regenerate the task plan.").strip()
        mode = "plan"
    else:
        plan = repository.get_latest_task_plan(user["id"], task_id)
        if plan is None or plan.get("status") != "approved":
            raise HTTPException(status_code=409, detail="approved plan required before retry")
        session_service.enter_execute_mode(user["id"], task["session_id"])
        instruction = (req.message or "Retry the approved plan from the failed step.").strip()
        message = f"{instruction}\n\nApproved plan:\n{plan['content']}"
        mode = "execute"

    return await chat_routes.chat(
        chat_routes.ChatRequest(
            message=message,
            session_id=task["session_id"],
            request_id=request_id,
            interaction_type="agent",
            mode=mode,
        ),
        request,
        user,
    )
