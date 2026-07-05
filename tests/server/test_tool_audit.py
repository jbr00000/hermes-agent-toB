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
