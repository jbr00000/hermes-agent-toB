from __future__ import annotations

from server import audit


def test_audit_records_structured_event(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(audit, "_DB_PATH", None)

    audit.init_db()
    event_id = audit.record_event(
        event_type="chat_turn",
        session_id="s1",
        user_id="u1",
        status="completed",
        mode="execute",
        metadata={"provider": "deepseek", "enabled_toolsets": ["db", "terminal"]},
    )

    events = audit.list_events(session_id="s1")
    assert event_id > 0
    assert len(events) == 1
    assert events[0]["event_type"] == "chat_turn"
    assert events[0]["metadata"] == {
        "provider": "deepseek",
        "enabled_toolsets": ["db", "terminal"],
    }
    assert events[0]["error"] is None
