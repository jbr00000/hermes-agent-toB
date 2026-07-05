"""Append-only audit log for the headless server."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from hermes_constants import get_hermes_home

_DB_PATH: str | None = None
_LOCK = threading.Lock()


def _db_path() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = str(get_hermes_home() / "audit.db")
    return _DB_PATH


def init_db() -> None:
    with _LOCK:
        con = sqlite3.connect(_db_path())
        try:
            con.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    session_id TEXT,
                    user_id TEXT,
                    status TEXT NOT NULL,
                    mode TEXT,
                    metadata TEXT,
                    error TEXT,
                    created_at REAL NOT NULL
                )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_session_id "
                "ON audit_events(session_id, created_at)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_user_id "
                "ON audit_events(user_id, created_at)"
            )
            con.commit()
        finally:
            con.close()


def record_event(
    *,
    event_type: str,
    session_id: str | None,
    user_id: str | None,
    status: str,
    mode: str | None = None,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> int:
    init_db()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        con = sqlite3.connect(_db_path())
        try:
            cur = con.execute(
                """INSERT INTO audit_events (
                    event_type, session_id, user_id, status, mode, metadata, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_type,
                    session_id,
                    user_id,
                    status,
                    mode,
                    metadata_json,
                    error,
                    time.time(),
                ),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_events(*, session_id: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    clauses = []
    params: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _LOCK:
        con = sqlite3.connect(_db_path())
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT * FROM audit_events" + where + " ORDER BY created_at, id",
                params,
            ).fetchall()
        finally:
            con.close()
    events = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        events.append(item)
    return events
