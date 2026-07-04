"""Per-user persistent memory (SQLite @ HERMES_HOME/memory.db).

Decision 7: persistent memory + cross-session accumulation, LOCAL (all 8 cloud
memory providers were removed in Step 2.3 — data stays in the customer env).
Each user's memories are isolated by user_id (the same boundary the sessions
use). Inc 1: explicit memories (the user POSTs them, or an admin does on their
behalf); automatic extraction (an LLM decides what to remember after each turn)
is Inc 2 — the nudge loop the user wants to refine later.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from typing import List

from hermes_constants import get_hermes_home

_DB_PATH_LOCK = threading.Lock()
_db_path_cache: str | None = None


def _db_path() -> str:
    global _db_path_cache
    if _db_path_cache is None:
        with _DB_PATH_LOCK:
            if _db_path_cache is None:
                _db_path_cache = str(get_hermes_home() / "memory.db")
    return _db_path_cache


def init_db() -> None:
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "  id TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  content TEXT NOT NULL,"
            "  created_at REAL NOT NULL"
            ")"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)")
        con.commit()
    finally:
        con.close()


def save_memory(user_id: str, content: str) -> dict:
    con = sqlite3.connect(_db_path())
    try:
        mid = str(uuid.uuid4())
        now = time.time()
        con.execute(
            "INSERT INTO memories(id, user_id, content, created_at) VALUES(?,?,?,?)",
            (mid, user_id, content, now),
        )
        con.commit()
        return {"id": mid, "user_id": user_id, "content": content, "created_at": now}
    finally:
        con.close()


def list_memories(user_id: str) -> List[dict]:
    con = sqlite3.connect(_db_path())
    try:
        rows = con.execute(
            "SELECT id, content, created_at FROM memories WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]
    finally:
        con.close()


def list_memory_contents(user_id: str) -> List[str]:
    """Just the content strings, for injection into the agent's prompt."""
    return [m["content"] for m in list_memories(user_id)]


def delete_memory(user_id: str, memory_id: str) -> bool:
    con = sqlite3.connect(_db_path())
    try:
        cur = con.execute(
            "DELETE FROM memories WHERE id=? AND user_id=?", (memory_id, user_id)
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()
