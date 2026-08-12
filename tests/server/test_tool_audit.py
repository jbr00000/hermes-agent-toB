from __future__ import annotations

import json

from tools.registry import registry


def test_handle_function_call_records_tool_audit(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from server import audit

    monkeypatch.setattr(audit, "_DB_PATH", None)
    audit.init_db()

    def handler(args, **_kwargs):
        assert args["secret"] == "should-not-be-logged"
        return json.dumps({"ok": True, "rows": [1, 2, 3]})

    registry.register(
        name="stage3_audit_probe",
        toolset="db",
        schema={"name": "stage3_audit_probe", "description": "test tool", "parameters": {}},
        handler=handler,
    )
    try:
        from model_tools import handle_function_call

        result = json.loads(
            handle_function_call(
                "stage3_audit_probe",
                {"sql": "SELECT * FROM private_table", "secret": "should-not-be-logged"},
                session_id="s1",
                user_id="u1",
                task_id="task-1",
                tool_call_id="call-1",
            )
        )
    finally:
        registry.deregister("stage3_audit_probe")

    assert result == {"ok": True, "rows": [1, 2, 3]}

    events = audit.list_events(session_id="s1")
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "tool_call"
    assert event["user_id"] == "u1"
    assert event["status"] == "completed"
    assert event["metadata"]["tool_name"] == "stage3_audit_probe"
    assert event["metadata"]["tool_call_id"] == "call-1"
    assert event["metadata"]["task_id"] == "task-1"
    assert event["metadata"]["duration_ms"] >= 0
    assert event["metadata"]["args"] == {
        "keys": ["secret", "sql"],
        "sql_fingerprint": event["metadata"]["args"]["sql_fingerprint"],
    }
    assert "private_table" not in json.dumps(event["metadata"], ensure_ascii=False)
    assert "should-not-be-logged" not in json.dumps(event["metadata"], ensure_ascii=False)


def test_handle_function_call_records_failed_tool_audit(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from server import audit

    monkeypatch.setattr(audit, "_DB_PATH", None)
    audit.init_db()

    def handler(_args, **_kwargs):
        raise RuntimeError("sensitive backend detail")

    registry.register(
        name="stage3_audit_failure_probe",
        toolset="db",
        schema={"name": "stage3_audit_failure_probe", "description": "test tool", "parameters": {}},
        handler=handler,
    )
    try:
        from model_tools import handle_function_call

        result = json.loads(
            handle_function_call(
                "stage3_audit_failure_probe",
                {"sql": "SELECT secret FROM customers"},
                session_id="s1",
            )
        )
    finally:
        registry.deregister("stage3_audit_failure_probe")

    assert "error" in result

    events = audit.list_events(session_id="s1")
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["event_type"] == "tool_call"
    assert "RuntimeError" in (events[0]["error"] or "")


def test_summarize_tool_args_records_web_query_and_redacted_urls() -> None:
    from server.audit import summarize_tool_args

    search = summarize_tool_args({"query": "2026 AI 政策", "limit": 5}, "web_search")
    assert search["query"] == "2026 AI 政策"
    assert search["keys"] == ["limit", "query"]

    extract = summarize_tool_args(
        {"urls": ["https://example.com/a?token=s3cret&lang=zh", "http://192.168.1.1"]},
        "web_extract",
    )
    assert extract["urls"] == ["https://example.com/a?token=***&lang=zh", "http://192.168.1.1"]
    assert "s3cret" not in json.dumps(extract, ensure_ascii=False)

    # 其他工具不记录参数值（现状不变）
    other = summarize_tool_args({"query": "x"}, "db_query")
    assert "query" not in other


def test_observer_marks_security_blocks_as_blocked() -> None:
    from model_tools import _tool_result_observer_fields

    # 顶层 Blocked（如 URL 携带凭证被拦）
    assert _tool_result_observer_fields(
        json.dumps({"success": False, "error": "Blocked: URL contains a credential-like query parameter (token)."})
    )[0] == "blocked"

    # per-entry 全部被 SSRF 拦截（web_extract 的内网地址场景）
    assert _tool_result_observer_fields(
        json.dumps({"results": [
            {"url": "http://192.168.1.1", "error": "Blocked: URL targets a private or internal network address"},
            {"url": "http://169.254.169.254", "error": "Blocked: URL targets a private or internal network address"},
        ]})
    )[0] == "blocked"

    # 部分成功部分被拦 → 整体仍是 completed（结果里有可用内容）
    assert _tool_result_observer_fields(
        json.dumps({"results": [
            {"url": "https://example.com", "content": "ok", "error": ""},
            {"url": "http://192.168.1.1", "error": "Blocked: URL targets a private or internal network address"},
        ]})
    )[0] == "ok"

    # 普通工具错误仍是 failed
    assert _tool_result_observer_fields(json.dumps({"error": "boom"}))[0] == "error"
