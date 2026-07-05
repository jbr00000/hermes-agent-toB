"""Session management for the headless server — per-user via SessionDB.

Every headless session is stored with source='headless' + the authenticated
user_id. That pair is the isolation boundary (decision 2/3): a user can only
list / read / resume sessions they own. SessionDB persists across server
restarts (SQLite at HERMES_HOME), so conversations survive.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from hermes_constants import get_hermes_home
from hermes_state import SessionDB

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

_db: Optional[SessionDB] = None
_db_lock = threading.Lock()


def get_session_db() -> SessionDB:
    """Lazily build a process-wide SessionDB (DEFAULT_DB_PATH under HERMES_HOME)."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = SessionDB(db_path=get_hermes_home() / "state.db")
    return _db


def list_user_sessions(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List this user's headless sessions, newest first (top-level only)."""
    db = get_session_db()
    with db._lock:  # reuse SessionDB's own guard around its connection
        rows = db._conn.execute(
            "SELECT id, model, started_at, ended_at FROM sessions "
            "WHERE source = ? AND user_id = ? AND parent_session_id IS NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (SOURCE, user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_owned_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Return the session row iff it is a headless session owned by user_id."""
    session = get_session_db().get_session(session_id)
    if session is None:
        return None
    if session.get("source") != SOURCE or session.get("user_id") != user_id:
        return None
    return session


def get_owned_messages(user_id: str, session_id: str) -> Optional[List[Dict[str, Any]]]:
    """Return the messages of an owned session (None if not found / not owned)."""
    if get_owned_session(user_id, session_id) is None:
        return None
    return get_session_db().get_messages(session_id)


def assert_session_owned(user_id: str, session_id: str) -> None:
    """Gate for /chat resume-by-session_id.

    A brand-new session_id (not yet in the DB) is allowed — the AIAgent will
    create the row on its first turn. An EXISTING session must belong to the
    authenticated user, else 403 (prevents cross-user session hijack).
    """
    existing = get_session_db().get_session(session_id)
    if existing is not None and (
        existing.get("source") != SOURCE or existing.get("user_id") != user_id
    ):
        raise HTTPException(status_code=403, detail="session does not belong to this user")


def _ensure_plan_table(db: SessionDB | None = None) -> None:
    db = db or get_session_db()
    with db._lock:
        db._conn.execute(
            """CREATE TABLE IF NOT EXISTS headless_session_modes (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                state TEXT NOT NULL,
                approved_at REAL,
                updated_at REAL NOT NULL
            )"""
        )
        db._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_headless_session_modes_user "
            "ON headless_session_modes(user_id, updated_at)"
        )
        db._conn.commit()


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
    db = get_session_db()
    _ensure_plan_table(db)
    with db._lock:
        row = db._conn.execute(
            "SELECT session_id, user_id, state, approved_at, updated_at "
            "FROM headless_session_modes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def _set_mode_state(
    user_id: str,
    session_id: str,
    state: str,
    *,
    approved_at: float | None = None,
) -> dict[str, Any]:
    if state not in PLAN_STATES:
        raise ValueError(f"invalid session mode state: {state}")
    db = get_session_db()
    _ensure_plan_table(db)
    now = time.time()
    if approved_at is None and state in {PLAN_PENDING, EXECUTE}:
        approved_at_value = None
    else:
        approved_at_value = approved_at
    with db._lock:
        db._conn.execute(
            """INSERT INTO headless_session_modes(
                session_id, user_id, state, approved_at, updated_at
            ) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                state = excluded.state,
                approved_at = excluded.approved_at,
                updated_at = excluded.updated_at""",
            (session_id, user_id, state, approved_at_value, now),
        )
        db._conn.commit()
    return {
        "session_id": session_id,
        "user_id": user_id,
        "state": state,
        "approved_at": approved_at_value,
        "updated_at": now,
    }


def get_session_mode(user_id: str, session_id: str) -> dict[str, Any]:
    """Return the server-side plan/execute state for an owned session."""
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
    """Put an owned session into plan_pending state."""
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row and row.get("state") == EXECUTE:
        raise HTTPException(status_code=409, detail="execute session cannot return to plan mode")
    return _set_mode_state(user_id, session_id, PLAN_PENDING)


def approve_plan(user_id: str, session_id: str) -> dict[str, Any]:
    """Approve a plan session. Execution still requires enter_execute_mode."""
    if get_owned_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = _get_mode_row(session_id)
    if row is None or row.get("state") not in {PLAN_PENDING, PLAN_APPROVED}:
        raise HTTPException(status_code=409, detail="session is not waiting for plan approval")
    approved_at = row.get("approved_at") or time.time()
    return _set_mode_state(user_id, session_id, PLAN_APPROVED, approved_at=approved_at)


def enter_execute_mode(user_id: str, session_id: str) -> dict[str, Any]:
    """Switch an approved plan session into execute mode."""
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
        user_id,
        session_id,
        EXECUTE,
        approved_at=row.get("approved_at"),
    )


def resolve_chat_mode(user_id: str, session_id: str, requested_mode: str | None) -> dict[str, Any]:
    """Resolve the tool mode for /chat and enforce the approval gate.

    A client-supplied ``mode=execute`` is only a request. Existing plan
    sessions remain in read-only PLAN mode until the server-side approve and
    execute endpoints advance the state.
    """
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
