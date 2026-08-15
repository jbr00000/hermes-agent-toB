"""强制改密（must_change_password）全链路测试。

覆盖：建号/管理员重置 → 登录返回 flag → POST /auth/change-password
（旧密码错 400 / 新密码过短 422 / 成功清 flag + 旧 refresh 吊销 + 新 token 可用）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


_ADMIN_PASSWORD = "correct-horse-battery-staple"


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", _ADMIN_PASSWORD)
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server import auth
    from server.storage import reset_storage_for_tests
    from server.storage.runtime import reset_runtime_store_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()
    reset_runtime_store_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> tuple[dict[str, str], dict]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


def _create_user(client: TestClient, username: str = "worker", password: str = "initial-pass-1") -> dict:
    admin_headers, _ = _login(client, "admin", _ADMIN_PASSWORD)
    created = client.post(
        "/users",
        headers=admin_headers,
        json={"username": username, "password": password, "role": "user"},
    )
    assert created.status_code == 200, created.text
    return created.json()["user"]


def test_created_user_must_change_password(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    _create_user(client)

    _, user = _login(client, "worker", "initial-pass-1")
    assert user["must_change_password"] is True

    # 管理员自助改的密码不受强制改密约束（bootstrapped superadmin 不带 flag）。
    _, admin_user = _login(client, "admin", _ADMIN_PASSWORD)
    assert admin_user["must_change_password"] is False


def test_change_password_wrong_old_password_400(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    _create_user(client)
    headers, _ = _login(client, "worker", "initial-pass-1")

    response = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "wrong-old-password", "new_password": "new-password-1"},
    )
    assert response.status_code == 400

    # 旧密码仍然有效。
    _, user = _login(client, "worker", "initial-pass-1")
    assert user["must_change_password"] is True


def test_change_password_too_short_422(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    _create_user(client)
    headers, _ = _login(client, "worker", "initial-pass-1")

    response = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "initial-pass-1", "new_password": "short"},
    )
    assert response.status_code == 422


def test_change_password_success_clears_flag_and_revokes_old_refresh(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, tmp_path)
    _create_user(client)
    login_response = client.post(
        "/auth/login", json={"username": "worker", "password": "initial-pass-1"}
    )
    old_refresh = login_response.cookies.get("hermes_refresh_token")
    assert old_refresh
    headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    changed = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "initial-pass-1", "new_password": "new-password-1"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["user"]["must_change_password"] is False
    assert body["access_token"]

    # 改密签发的旧 refresh 会话已吊销。
    stale = client.post("/auth/refresh", cookies={"hermes_refresh_token": old_refresh})
    assert stale.status_code == 401

    # 新密码可登录，旧密码不可。
    assert (
        client.post("/auth/login", json={"username": "worker", "password": "initial-pass-1"})
        .status_code
        == 401
    )
    _, user = _login(client, "worker", "new-password-1")
    assert user["must_change_password"] is False


def test_admin_reset_sets_flag_again(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create_user(client)
    headers, _ = _login(client, "worker", "initial-pass-1")
    changed = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "initial-pass-1", "new_password": "new-password-1"},
    )
    assert changed.status_code == 200

    admin_headers, _ = _login(client, "admin", _ADMIN_PASSWORD)
    reset = client.put(
        f"/users/{created['id']}/password",
        headers=admin_headers,
        json={"password": "reset-pass-123"},
    )
    assert reset.status_code == 200, reset.text

    _, user = _login(client, "worker", "reset-pass-123")
    assert user["must_change_password"] is True
