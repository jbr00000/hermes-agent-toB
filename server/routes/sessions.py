"""Session routes: GET /sessions, GET /sessions/{id}, POST /sessions/{id}/resume.

All scoped to the authenticated user (per-user isolation). Resume itself
happens by POSTing to /chat with the session_id; this endpoint just verifies
ownership and returns the session so the client can confirm it's resumable.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import sessions as sess
from server.deps import get_current_user
from server.storage import get_repository, get_runtime_store

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    interaction_type: Literal["chat", "agent"] = "chat"
    title: str | None = Field(default=None, max_length=100)


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    pinned: bool | None = None
    archived: bool | None = None


@router.post("", status_code=201)
def create_session(req: CreateSessionRequest, user: dict = Depends(get_current_user)):
    return {
        "session": sess.create_user_session(
            user["id"], interaction_type=req.interaction_type, title=req.title
        )
    }


@router.get("")
def list_sessions(
    interaction_type: Literal["chat", "agent"] | None = None,
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    return {
        "sessions": sess.list_user_sessions(
            user["id"],
            limit,
            interaction_type=interaction_type,
            include_archived=include_archived,
        )
    }


@router.get("/{session_id}")
def get_session_detail(session_id: str, user: dict = Depends(get_current_user)):
    session = sess.get_owned_session(user["id"], session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = sess.get_owned_messages(user["id"], session_id) or []
    active_run = sess.get_active_run(user["id"], session_id)
    if active_run is not None:
        snapshot = get_runtime_store().get_chat_snapshot(active_run["id"])
        if snapshot and snapshot.get("conversation_id") == session_id:
            active_run = {
                **active_run,
                "partial_content": snapshot.get("content") or "",
                "sequence": int(snapshot.get("sequence") or 0),
                "snapshot_updated_at": snapshot.get("updated_at"),
            }
    return {"session": session, "messages": messages, "active_run": active_run}


@router.delete("/{session_id}")
def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    repository = get_repository()
    task = repository.get_task_by_conversation(user["id"], session_id)
    result = (
        repository.delete_owned_task(user["id"], task["id"])
        if task is not None
        else sess.delete_owned_session(user["id"], session_id)
    )
    if result == "missing":
        raise HTTPException(status_code=404, detail="session not found")
    if result == "running":
        raise HTTPException(status_code=409, detail="running session cannot be deleted")
    if task is not None:
        from server.sandbox import destroy_task_sandbox

        destroy_task_sandbox(user["id"], task["id"])
    return {"deleted": session_id}


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    req: UpdateSessionRequest,
    user: dict = Depends(get_current_user),
):
    changes = {
        field: getattr(req, field)
        for field in req.model_fields_set
        if field in {"title", "pinned", "archived"}
    }
    session = sess.update_owned_session(user["id"], session_id, **changes)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session": session}


@router.post("/{session_id}/resume")
def resume_session(session_id: str, user: dict = Depends(get_current_user)):
    session = sess.get_owned_session(user["id"], session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "resumable": True}


@router.get("/{session_id}/mode")
def get_mode(session_id: str, user: dict = Depends(get_current_user)):
    return {"mode": sess.get_session_mode(user["id"], session_id)}


@router.post("/{session_id}/plan")
def start_plan(session_id: str, user: dict = Depends(get_current_user)):
    return {"mode": sess.start_plan_mode(user["id"], session_id)}


@router.post("/{session_id}/approve")
def approve_plan(session_id: str, user: dict = Depends(get_current_user)):
    return {"mode": sess.approve_plan(user["id"], session_id)}


@router.post("/{session_id}/execute")
def execute_plan(session_id: str, user: dict = Depends(get_current_user)):
    return {"mode": sess.enter_execute_mode(user["id"], session_id)}
