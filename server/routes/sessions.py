"""Session routes: GET /sessions, GET /sessions/{id}, POST /sessions/{id}/resume.

All scoped to the authenticated user (per-user isolation). Resume itself
happens by POSTing to /chat with the session_id; this endpoint just verifies
ownership and returns the session so the client can confirm it's resumable.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server import sessions as sess
from server.deps import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def list_sessions(user: dict = Depends(get_current_user)):
    return {"sessions": sess.list_user_sessions(user["id"])}


@router.get("/{session_id}")
def get_session_detail(session_id: str, user: dict = Depends(get_current_user)):
    session = sess.get_owned_session(user["id"], session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = sess.get_owned_messages(user["id"], session_id) or []
    return {"session": session, "messages": messages}


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
