"""Append-only audit facade backed by the shared storage repository."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from server.storage import get_repository, init_storage

_DB_PATH: str | None = None  # Backward-compatible test reset hook.
_ERROR_TYPE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*Error)\b")


def init_db() -> None:
    init_storage()


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
    return get_repository().record_audit_event(
        event_type=event_type,
        conversation_id=session_id,
        user_id=user_id,
        status=status,
        mode=mode,
        metadata=metadata or {},
        error=error,
    )


def summarize_tool_args(args: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {"keys": []}
    keys = sorted(str(key) for key in args.keys())
    summary: dict[str, Any] = {"keys": keys}
    sql = args.get("sql")
    if isinstance(sql, str) and sql.strip():
        normalized = " ".join(sql.split()).lower()
        summary["sql_fingerprint"] = hashlib.sha256(
            normalized.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
    return summary


def summarize_tool_error(error: str | None) -> str | None:
    if not error:
        return None
    match = _ERROR_TYPE_RE.search(error)
    if match:
        return match.group(1)
    return str(error).splitlines()[0][:160]


def record_tool_call(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    session_id: str | None,
    user_id: str | None = None,
    mode: str | None = None,
    status: str,
    duration_ms: int = 0,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    error: str | None = None,
) -> int | None:
    if not session_id:
        return None
    audit_status = {
        "ok": "completed",
        "error": "failed",
        "blocked": "blocked",
    }.get(status, status or "completed")
    return record_event(
        event_type="tool_call",
        session_id=session_id,
        user_id=user_id,
        status=audit_status,
        mode=mode,
        metadata={
            "tool_name": tool_name,
            "args": summarize_tool_args(args),
            "duration_ms": max(0, int(duration_ms or 0)),
            "task_id": task_id,
            "tool_call_id": tool_call_id,
        },
        error=summarize_tool_error(error),
    )


def list_events(
    *, session_id: str | None = None, user_id: str | None = None
) -> list[dict[str, Any]]:
    init_db()
    return get_repository().list_audit_events(
        conversation_id=session_id, user_id=user_id
    )
