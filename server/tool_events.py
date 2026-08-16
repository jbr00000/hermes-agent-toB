from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal

ToolRiskLevel = Literal["read", "controlled_write", "high_risk", "unknown"]

_READ_TOOLS = {"db_query", "knowledge_search"}
_HIGH_RISK_TOOLS = {"terminal", "process", "code_execution"}
_CONTROLLED_WRITE_PREFIXES = ("write_", "create_", "update_", "delete_", "patch_")


def tool_risk_level(tool_name: str | None) -> ToolRiskLevel:
    normalized = (tool_name or "").strip().lower()
    if normalized in _READ_TOOLS:
        return "read"
    if normalized in _HIGH_RISK_TOOLS:
        return "high_risk"
    if normalized.startswith(_CONTROLLED_WRITE_PREFIXES):
        return "controlled_write"
    return "unknown"


def _fingerprint(value: str) -> str:
    normalized = " ".join(value.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def summarize_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        keys = sorted(str(key)[:64] for key in arguments.keys())[:50]
        summary: dict[str, Any] = {
            "kind": "object",
            "keys": keys,
            "field_count": len(arguments),
        }
        sql = arguments.get("sql") or arguments.get("query")
        if isinstance(sql, str) and sql.strip():
            summary["sql_fingerprint"] = _fingerprint(sql)
        return summary
    if isinstance(arguments, Sequence) and not isinstance(arguments, (str, bytes, bytearray)):
        return {"kind": "array", "item_count": len(arguments)}
    return {"kind": type(arguments).__name__}


def summarize_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return {
            "kind": "object",
            "field_count": len(result),
        }
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return {"kind": "array", "item_count": len(result)}
    if isinstance(result, str):
        return {"kind": "text", "character_count": len(result)}
    if result is None:
        return {"kind": "null"}
    return {"kind": type(result).__name__}


def sanitize_tool_event_payload(
    event_type: str,
    tool_name: str | None,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = payload or {}
    sanitized: dict[str, Any] = {}
    tool_call_id = source.get("tool_call_id")
    if tool_call_id is not None:
        sanitized["tool_call_id"] = str(tool_call_id)[:128]

    if "arguments" in source:
        sanitized["arguments"] = summarize_tool_arguments(source.get("arguments"))
    if event_type == "tool.completed" and "result" in source:
        sanitized["result"] = summarize_tool_result(source.get("result"))
    if "duration_ms" in source:
        try:
            sanitized["duration_ms"] = max(0, int(source.get("duration_ms") or 0))
        except (TypeError, ValueError):
            sanitized["duration_ms"] = 0
    if event_type == "tool.progress":
        metadata = source.get("metadata")
        if isinstance(metadata, Mapping):
            sanitized["metadata_keys"] = sorted(
                str(key)[:64] for key in metadata.keys()
            )[:50]

    sanitized["risk_level"] = tool_risk_level(tool_name)
    return sanitized
