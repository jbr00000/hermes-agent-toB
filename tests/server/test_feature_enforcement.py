"""Per-user feature flags must be enforced server-side on every write path."""
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


def _create_limited_user(
    client: TestClient, headers: dict[str, str], username: str, **features: bool
) -> dict[str, str]:
    response = client.post(
        "/users",
        headers=headers,
        json={"username": username, "password": "password-123", "features": features},
    )
    assert response.status_code == 200, response.text
    headers = _login(client, username, "password-123")
    # 新建用户带 must_change_password 标记（白名单外 403）；走真实改密流程清掉，
    # 以免与 feature 门控的 403 混淆。
    changed = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200, changed.text
    return {"Authorization": f"Bearer {changed.json()['access_token']}"}


def test_agent_feature_off_blocks_agent_paths(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    headers = _create_limited_user(client, super_headers, "no-agent", agent=False)

    # /chat with agent semantics → 403 before any conversation is created.
    denied = client.post(
        "/chat", headers=headers, json={"message": "hi", "interaction_type": "agent"}
    )
    assert denied.status_code == 403
    assert "agent" in denied.json()["detail"]

    assert client.get("/tasks", headers=headers).status_code == 403

    created = client.post(
        "/sessions", headers=headers, json={"interaction_type": "agent"}
    )
    assert created.status_code == 403

    # Chat paths still work (feature defaults to enabled).
    assert (
        client.post(
            "/sessions", headers=headers, json={"interaction_type": "chat"}
        ).status_code
        == 201
    )


def test_chat_feature_off_blocks_chat_paths(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    headers = _create_limited_user(client, super_headers, "no-chat", chat=False)

    denied = client.post(
        "/chat", headers=headers, json={"message": "hi", "interaction_type": "chat"}
    )
    assert denied.status_code == 403
    assert "chat" in denied.json()["detail"]

    created = client.post(
        "/sessions", headers=headers, json={"interaction_type": "chat"}
    )
    assert created.status_code == 403

    # Agent paths are NOT blocked by the chat flag (features are independent).
    assert (
        client.post(
            "/sessions", headers=headers, json={"interaction_type": "agent"}
        ).status_code
        == 201
    )

    # Read paths stay open so the user can still export their own history.
    assert client.get("/sessions", headers=headers).status_code == 200


def test_memory_feature_off_blocks_memory_routes(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    headers = _create_limited_user(client, super_headers, "no-memory", memory=False)

    denied = client.get("/memory", headers=headers)
    assert denied.status_code == 403
    assert "memory" in denied.json()["detail"]
    assert client.get("/memory/candidates", headers=headers).status_code == 403


def test_knowledge_feature_off_blocks_read_routes(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    headers = _create_limited_user(client, super_headers, "no-kb", knowledge=False)

    denied = client.get("/knowledge/documents", headers=headers)
    assert denied.status_code == 403
    assert "knowledge" in denied.json()["detail"]


def test_knowledge_feature_off_blocks_admin_routes_for_admin(
    monkeypatch, tmp_path
) -> None:
    """knowledge 开关对所有角色生效：admin 被关掉后连管理端点也 403。"""
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    created = client.post(
        "/users",
        headers=super_headers,
        json={
            "username": "kb-admin",
            "password": "password-123",
            "role": "admin",
            "features": {"knowledge": False},
        },
    )
    assert created.status_code == 200, created.text
    headers = _login(client, "kb-admin", "password-123")
    changed = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200, changed.text
    headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    denied = client.post("/knowledge/bases", headers=headers, json={"name": "x"})
    assert denied.status_code == 403
    assert "knowledge" in denied.json()["detail"]
    assert client.get("/knowledge/documents", headers=headers).status_code == 403


def test_feature_change_takes_effect_without_relogin(monkeypatch, tmp_path) -> None:
    """Features are read from the user row per request, not baked into the JWT."""
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    headers = _create_limited_user(client, super_headers, "flip")
    me = client.get("/auth/me", headers=headers).json()["user"]

    assert client.get("/memory", headers=headers).status_code == 200

    client.put(
        f"/users/{me['id']}/features",
        headers=super_headers,
        json={"memory": False},
    )
    assert client.get("/memory", headers=headers).status_code == 403
