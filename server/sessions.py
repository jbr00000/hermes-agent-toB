"""User-scoped Chat/Agent conversation management."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server.storage import get_repository

SOURCE = "headless"
PLAN_PENDING = "plan_pending"
PLAN_APPROVED = "plan_approved"
EXECUTE = "execute"
PLAN_STATES = {PLAN_PENDING, PLAN_APPROVED, EXECUTE}
_TOOL_MODE_BY_STATE = {
    PLAN_PENDING: "plan",
    PLAN_APPROVED: "plan",
    EXECUTE: "execute",
}


class HeadlessSessionStore:
    """Compatibility adapter for the subset of SessionDB used by the server."""

    def create_session(self, session_id: str, source: str, **kwargs: Any) -> str:
        model_config = kwargs.get("model_config")
        if isinstance(model_config, str):
            try:
                model_config = json.loads(model_config)
            except json.JSONDecodeError:
                model_config = {}
        get_repository().ensure_conversation(
            session_id,
            kwargs.get("user_id") or "",
            source=source,
            interaction_type=kwargs.get("interaction_type") or "agent",
            title=kwargs.get("title"),
            model=kwargs.get("model"),
            model_config=model_config or {},
        )
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = get_repository().get_conversation(session_id)
        if row is None:
            return None
        result = dict(row)
        result["model_config"] = json.dumps(row.get("model_config") or {}, ensure_ascii=False)
        result["message_count"] = get_repository().get_message_count(session_id)
        return result

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        return get_repository().get_messages(session_id)

    def get_messages_as_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        return [
            {"role": message["role"], "content": message["content"]}
            for message in self.get_messages(session_id)
        ]

    def append_message(self, session_id: str, role: str, content: Any = None, **kwargs: Any):
        return get_repository().append_message(
            session_id,
            role,
            content or "",
            created_at=kwargs.get("timestamp"),
        )["id"]

    def update_session_meta(
        self, session_id: str, model_config_json: str, model: str | None = None
    ) -> None:
        try:
            model_config = json.loads(model_config_json) if model_config_json else {}
        except json.JSONDecodeError:
            model_config = {}
        get_repository().update_conversation_model(
            session_id, model=model, model_config=model_config
        )

    def get_session_title(self, session_id: str) -> str | None:
        row = get_repository().get_conversation(session_id)
        return row.get("title") if row else None

    def set_session_title(self, session_id: str, title: str) -> None:
        get_repository().set_conversation_title(session_id, title)


_db: HeadlessSessionStore | None = None
_db_lock = threading.Lock()


def get_session_db() -> HeadlessSessionStore:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = HeadlessSessionStore()
    return _db


def create_user_session(
    user_id: str,
    *,
    interaction_type: str = "chat",
    title: str | None = None,
) -> dict[str, Any]:
    return get_repository().create_conversation(
        user_id, interaction_type=interaction_type, title=title, source=SOURCE
    )


def list_user_sessions(
    user_id: str,
    limit: int = 50,
    *,
    interaction_type: str | None = None,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    return get_repository().list_conversations(
        user_id,
        interaction_type=interaction_type,
        include_archived=include_archived,
        limit=max(1, min(limit, 100)),
    )


def get_owned_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    return get_repository().get_owned_conversation(user_id, session_id)


def get_owned_messages(user_id: str, session_id: str) -> Optional[List[Dict[str, Any]]]:
    if get_owned_session(user_id, session_id) is None:
        return None
    return get_repository().get_messages(session_id)


def get_active_run(user_id: str, session_id: str) -> dict[str, Any] | None:
    if get_owned_session(user_id, session_id) is None:
        return None
    return get_repository().get_active_model_run(user_id, session_id)


def update_owned_session(user_id: str, session_id: str, **changes: Any) -> dict[str, Any] | None:
    return get_repository().update_conversation(user_id, session_id, **changes)


def delete_owned_session(user_id: str, session_id: str) -> str:
    return get_repository().delete_owned_conversation(user_id, session_id)


def assert_session_owned(user_id: str, session_id: str) -> None:
    existing = get_repository().get_conversation(session_id)
    if existing is not None and existing.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="session does not belong to this user")


def _normalize_requested_mode(mode: str | None) -> str | None:
    if mode is None:
        return None
    normalized = str(mode).strip().lower()
    if normalized in {"", "auto"}:
        return None
    if normalized not in {"plan", "execute"}:
        raise HTTPException(status_code=400, detail="mode must be 'plan' or 'execute'")
    return normalized


def _get_mode_row(session_id: str) -> dict[str, Any] | None:
    return get_repository().get_plan_state(session_id)


def _set_mode_state(
    user_id: str,
    session_id: str,
    state: str,
    *,
    approved_at: float | None = None,
) -> dict[str, Any]:
    if state not in PLAN_STATES:
        raise ValueError(f"invalid session mode state: {state}")
    if approved_at is None and state in {PLAN_PENDING, EXECUTE}:
        approved_at = None
    return get_repository().set_plan_state(user_id, session_id, state, approved_at)


def get_session_mode(user_id: str, session_id: str) -> dict[str, Any]:
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row is None:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "state": EXECUTE,
            "approved_at": None,
            "updated_at": None,
        }
    if row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="session does not belong to this user")
    return row


def start_plan_mode(user_id: str, session_id: str) -> dict[str, Any]:
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row and row.get("state") == EXECUTE:
        raise HTTPException(status_code=409, detail="execute session cannot return to plan mode")
    return _set_mode_state(user_id, session_id, PLAN_PENDING)


def approve_plan(user_id: str, session_id: str) -> dict[str, Any]:
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row is None or row.get("state") not in {PLAN_PENDING, PLAN_APPROVED}:
        raise HTTPException(status_code=409, detail="session is not waiting for plan approval")
    return _set_mode_state(
        user_id, session_id, PLAN_APPROVED, approved_at=row.get("approved_at") or time.time()
    )


def enter_execute_mode(user_id: str, session_id: str) -> dict[str, Any]:
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row is None:
        return _set_mode_state(user_id, session_id, EXECUTE)
    if row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="session does not belong to this user")
    if row.get("state") == PLAN_PENDING:
        raise HTTPException(status_code=409, detail="plan must be approved before execute")
    if row.get("state") == EXECUTE:
        return row
    return _set_mode_state(
        user_id, session_id, EXECUTE, approved_at=row.get("approved_at")
    )


def resolve_chat_mode(
    user_id: str, session_id: str, requested_mode: str | None
) -> dict[str, Any]:
    assert_session_owned(user_id, session_id)
    requested = _normalize_requested_mode(requested_mode)
    row = _get_mode_row(session_id)

    if row is None:
        if requested == "plan":
            row = _set_mode_state(user_id, session_id, PLAN_PENDING)
        else:
            row = {
                "session_id": session_id,
                "user_id": user_id,
                "state": EXECUTE,
                "approved_at": None,
                "updated_at": None,
            }
        return {**row, "tool_mode": _TOOL_MODE_BY_STATE[row["state"]]}

    if row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="session does not belong to this user")
    state = row.get("state")
    if state == PLAN_PENDING and requested == "execute":
        raise HTTPException(status_code=409, detail="plan must be approved before execute")
    if state == PLAN_APPROVED and requested == "execute":
        raise HTTPException(status_code=409, detail="call execute endpoint before executing")
    if state == EXECUTE and requested == "plan":
        raise HTTPException(status_code=409, detail="execute session cannot return to plan mode")
    if state not in PLAN_STATES:
        raise HTTPException(status_code=409, detail="invalid session mode state")
    return {**row, "tool_mode": _TOOL_MODE_BY_STATE[state]}
