from __future__ import annotations

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


def _seed_audit_events(user_id: str) -> None:
    from server.storage import get_repository

    repository = get_repository()
    repository.record_audit_event(
        event_type="tool_call",
        conversation_id="conv-1",
        user_id=user_id,
        status="completed",
        mode="execute",
        metadata={"tool_name": "web_search", "args": {"keys": ["query"], "query": "旧事件"}},
        error=None,
    )
    repository.record_audit_event(
        event_type="tool_call",
        conversation_id="conv-1",
        user_id=user_id,
        status="blocked",
        mode="execute",
        metadata={"tool_name": "web_extract", "args": {"keys": ["urls"], "urls": ["http://192.168.1.1"]}},
        error="security_block",
    )
    repository.record_audit_event(
        event_type="session_delete",
        conversation_id="conv-2",
        user_id=user_id,
        status="completed",
        mode=None,
        metadata={},
        error=None,
    )


def test_audit_events_require_admin_role(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    assert client.get("/audit/events").status_code == 401

    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    client.post(
        "/users",
        headers=admin_headers,
        json={"username": "regular", "password": "password-123", "role": "user"},
    )
    user_headers = _login(client, "regular", "password-123")

    assert client.get("/audit/events", headers=user_headers).status_code == 403
    assert client.get("/audit/events", headers=admin_headers).status_code == 200


def test_audit_events_newest_first_with_filters_and_username(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")

    from server.storage import get_repository

    admin = get_repository().get_user_by_username("admin")
    assert admin is not None
    _seed_audit_events(admin["id"])

    response = client.get("/audit/events", headers=admin_headers)
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 3
    # 最新在前（session_delete 最后写入，应排第一）
    assert events[0]["event_type"] == "session_delete"
    assert events[-1]["event_type"] == "tool_call"
    assert all(event["username"] == "admin" for event in events)

    tool_only = client.get("/audit/events?event_type=tool_call", headers=admin_headers)
    assert [event["event_type"] for event in tool_only.json()["events"]] == ["tool_call", "tool_call"]
    blocked = tool_only.json()["events"][0]
    assert blocked["status"] == "blocked"
    assert blocked["metadata"]["tool_name"] == "web_extract"

    limited = client.get("/audit/events?limit=1", headers=admin_headers)
    assert len(limited.json()["events"]) == 1

    assert client.get("/audit/events?limit=0", headers=admin_headers).status_code == 422
