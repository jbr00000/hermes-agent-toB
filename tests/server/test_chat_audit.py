from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_chat_records_runtime_audit_and_session_metadata(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")

    from server import audit, auth, sessions

    monkeypatch.setattr(auth, "_DB_PATH", None)
    monkeypatch.setattr(auth, "_JWT_SECRET", None)
    monkeypatch.setattr(audit, "_DB_PATH", None)
    monkeypatch.setattr(sessions, "_db", None)

    from server.app import create_app
    from server.deps import get_current_user

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "username": "user",
        "role": "user",
    }

    class FakeAgent:
        provider = "custom"
        model = "llama-3.3"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "terminal"]

        def chat(self, message, stream_callback=None):
            if stream_callback:
                stream_callback("ok")
            return f"answer: {message}"

    import server.agent_factory as agent_factory

    monkeypatch.setattr(agent_factory, "build_agent", lambda **_kwargs: FakeAgent())

    client = TestClient(app)
    response = client.post(
        "/chat",
        json={"session_id": "s1", "message": "hello", "mode": "execute"},
    )

    assert response.status_code == 200
    events = audit.list_events(session_id="s1")
    assert [(event["event_type"], event["status"]) for event in events] == [
        ("chat_turn", "started"),
        ("chat_turn", "completed"),
    ]
    assert events[0]["metadata"]["provider"] == "custom"
    assert events[0]["metadata"]["model"] == "llama-3.3"
    assert events[0]["metadata"]["enabled_toolsets"] == ["db", "terminal"]

    session = sessions.get_session_db().get_session("s1")
    assert session["model"] == "llama-3.3"
    assert json.loads(session["model_config"]) == {
        "enabled_toolsets": ["db", "terminal"],
        "mode": "execute",
        "provider": "custom",
        "reasoning_config": {"enabled": False},
    }
