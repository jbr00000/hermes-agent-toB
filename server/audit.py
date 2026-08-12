"""Append-only audit facade backed by the shared storage repository."""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def _redact_url_for_audit(url: str) -> str:
    """Return *url* safe for the audit trail (credential-like query values masked).

    Web access is audited by design (docs/联网检索接入方案.md §6.3), but a URL
    can itself carry a secret (signed links, ``?token=…``). Reuse the web
    stack's own sensitive-param detector so both stay in agreement.
    """
    trimmed = url.strip()[:300]
    try:
        from tools.url_safety import sensitive_query_param_name

        sensitive_key = sensitive_query_param_name(trimmed)
    except Exception:
        sensitive_key = None
    if not sensitive_key:
        return trimmed
    try:
        parts = urlsplit(trimmed)
        pairs = [
            (key, "***" if key == sensitive_key else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit(parts._replace(query=urlencode(pairs, safe="*")))[:300]
    except ValueError:
        return trimmed


def summarize_tool_args(
    args: dict[str, Any] | None, tool_name: str | None = None
) -> dict[str, Any]:
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
    # Web 检索属于"外部访问"，审计要求可追溯 query/URL（docs/联网检索接入方案.md §6.3）。
    if tool_name == "web_search":
        query = args.get("query")
        if isinstance(query, str) and query.strip():
            summary["query"] = query.strip()[:200]
    elif tool_name == "web_extract":
        urls = args.get("urls")
        if isinstance(urls, list):
            summary["urls"] = [
                _redact_url_for_audit(u) for u in urls[:10] if isinstance(u, str)
            ]
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
            "args": summarize_tool_args(args, tool_name),
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
