from __future__ import annotations

from fastapi.testclient import TestClient


def _app_with_user(monkeypatch, tmp_path):
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
    return app


def test_plan_session_rejects_execute_chat_before_approval(monkeypatch, tmp_path) -> None:
    app = _app_with_user(monkeypatch, tmp_path)

    from server import sessions

    db = sessions.get_session_db()
    db.create_session("s-plan", "headless", user_id="u1", chat_id="s-plan")
    sessions.start_plan_mode("u1", "s-plan")

    response = TestClient(app).post(
        "/chat",
        json={"session_id": "s-plan", "message": "run it", "mode": "execute"},
    )

    assert response.status_code == 409
    assert "approved" in response.json()["detail"]


def test_approve_then_execute_switches_chat_tool_mode(monkeypatch, tmp_path) -> None:
    app = _app_with_user(monkeypatch, tmp_path)

    from server import sessions

    db = sessions.get_session_db()
    db.create_session("s-plan", "headless", user_id="u1", chat_id="s-plan")
    sessions.start_plan_mode("u1", "s-plan")

    client = TestClient(app)
    assert client.post("/sessions/s-plan/approve").status_code == 200
    assert client.post("/sessions/s-plan/execute").status_code == 200

    captured = {}

    class FakeAgent:
        provider = "custom"
        model = "llama-3.3"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "session_search", "terminal"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            return "done"

    import server.agent_factory as agent_factory

    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: captured.update(kwargs) or FakeAgent())

    response = client.post(
        "/chat",
        json={"session_id": "s-plan", "message": "run it", "mode": "execute"},
    )

    assert response.status_code == 200
    assert captured["mode"] == "execute"
