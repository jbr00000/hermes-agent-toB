"""Session management for the headless server — per-user via SessionDB.

Every headless session is stored with source='headless' + the authenticated
user_id. That pair is the isolation boundary (decision 2/3): a user can only
list / read / resume sessions they own. SessionDB persists across server
restarts (SQLite at HERMES_HOME), so conversations survive.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from hermes_state import SessionDB

SOURCE = "headless"

_db: Optional[SessionDB] = None
_db_lock = threading.Lock()


def get_session_db() -> SessionDB:
    """Lazily build a process-wide SessionDB (DEFAULT_DB_PATH under HERMES_HOME)."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = SessionDB()
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
