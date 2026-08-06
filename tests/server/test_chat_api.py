from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server import auth
    from server.storage import reset_storage_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_browser_auth_session_can_be_restored_and_revoked(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    anonymous = client.get("/auth/session")
    assert anonymous.status_code == 200
    assert anonymous.json() == {"authenticated": False}

    login = client.post(
        "/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert login.cookies.get("hermes_refresh_token")

    restored = client.get("/auth/session")
    assert restored.status_code == 200
    assert restored.json()["authenticated"] is True
    assert restored.json()["user"]["username"] == "admin"

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    assert client.get("/auth/session").json() == {"authenticated": False}


def test_chat_conversation_is_user_scoped_and_manageable(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")

    created = client.post(
        "/sessions",
        headers=admin_headers,
        json={"interaction_type": "chat"},
    )
    assert created.status_code == 201
    conversation = created.json()["session"]
    assert conversation["title"] == "新问答"
    assert conversation["interaction_type"] == "chat"

    updated = client.patch(
        f"/sessions/{conversation['id']}",
        headers=admin_headers,
        json={"title": "轨道检修规范", "pinned": True},
    )
    assert updated.status_code == 200
    assert updated.json()["session"]["title"] == "轨道检修规范"
    assert updated.json()["session"]["pinned"] is True

    created_user = client.post(
        "/users",
        headers=admin_headers,
        json={"username": "other", "password": "password-123", "role": "user"},
    )
    assert created_user.status_code == 200
    other_headers = _login(client, "other", "password-123")
    assert client.get(f"/sessions/{conversation['id']}", headers=other_headers).status_code == 404
    assert client.get("/sessions?interaction_type=chat", headers=other_headers).json() == {"sessions": []}


def test_chat_run_state_duration_and_permanent_delete(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    conversation = client.post(
        "/sessions",
        headers=admin_headers,
        json={"interaction_type": "chat"},
    ).json()["session"]

    from server.storage import get_repository

    repository = get_repository()
    user = repository.get_user_by_username("admin")
    assert user is not None
    repository.create_model_run("active-request", user["id"], conversation["id"])
    repository.update_conversation(user["id"], conversation["id"], status="running")

    running_detail = client.get(
        f"/sessions/{conversation['id']}", headers=admin_headers
    ).json()
    assert running_detail["active_run"]["id"] == "active-request"
    assert running_detail["active_run"]["status"] == "running"
    assert isinstance(running_detail["active_run"]["started_at"], float)
    assert running_detail["active_run"]["elapsed_ms"] >= 0

    blocked_delete = client.delete(
        f"/sessions/{conversation['id']}", headers=admin_headers
    )
    assert blocked_delete.status_code == 409

    repository.append_message(
        conversation["id"],
        "assistant",
        "已完成",
        model_run_id="active-request",
        duration_ms=1250,
    )
    repository.finish_model_run("active-request", status="completed")
    repository.update_conversation(user["id"], conversation["id"], status="idle")

    completed_detail = client.get(
        f"/sessions/{conversation['id']}", headers=admin_headers
    ).json()
    assert completed_detail["active_run"] is None
    assert completed_detail["messages"][-1]["model_run_id"] == "active-request"
    assert completed_detail["messages"][-1]["duration_ms"] == 1250

    deleted = client.delete(f"/sessions/{conversation['id']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": conversation["id"]}
    assert client.get(f"/sessions/{conversation['id']}", headers=admin_headers).status_code == 404
    assert repository.get_owned_model_run(user["id"], "active-request") is None
    delete_events = repository.list_audit_events(conversation_id=conversation["id"])
    assert delete_events[-1]["event_type"] == "session_delete"


def test_chat_stream_is_read_only_and_persists_messages(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    conversation = client.post(
        "/sessions",
        headers=headers,
        json={"interaction_type": "chat"},
    ).json()["session"]

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "session_search"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            if stream_callback:
                stream_callback("测试")
                stream_callback("完成")
            return "测试完成"

        def interrupt(self, message=None):
            captured["interrupted"] = message or True

    import server.agent_factory as agent_factory

    def build_agent(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr(agent_factory, "build_agent", build_agent)

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": conversation["id"],
            "request_id": "request-1",
            "interaction_type": "chat",
            "message": "请总结检修规范",
        },
    )

    assert response.status_code == 200
    assert captured["mode"] == "chat"
    assert "event: delta" in response.text
    assert "测试完成" in response.text

    duplicate = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": conversation["id"],
            "request_id": "request-1",
            "interaction_type": "chat",
            "message": "重复请求不应再次执行",
        },
    )
    assert duplicate.status_code == 409

    detail = client.get(f"/sessions/{conversation['id']}", headers=headers).json()
    assert [(message["role"], message["content"]) for message in detail["messages"]] == [
        ("user", "请总结检修规范"),
        ("assistant", "测试完成"),
    ]
    assert detail["session"]["title"] == "请总结检修规范"
    assert detail["active_run"] is None
    assert detail["messages"][-1]["model_run_id"] == "request-1"
    assert detail["messages"][-1]["duration_ms"] >= 0

    done_events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and '"session_id"' in line
    ]
    assert done_events[-1]["session_id"] == conversation["id"]
