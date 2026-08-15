"""Login brute-force protection: failure counting, lockout, and audit."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from server.storage.runtime import get_runtime_store, reset_runtime_store_for_tests


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    monkeypatch.delenv("HERMES_LOGIN_LOCKOUT_SECONDS", raising=False)

    from server import auth
    from server.storage import reset_storage_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()
    reset_runtime_store_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login_attempt(client: TestClient, username: str, password: str) -> int:
    return client.post(
        "/auth/login", json={"username": username, "password": password}
    ).status_code


def test_five_failures_lock_the_account(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    for _ in range(5):
        assert _login_attempt(client, "admin", "wrong-password") == 401
    # 第 6 次起：即使密码正确也是 429。
    assert _login_attempt(client, "admin", "wrong-password") == 429
    assert _login_attempt(client, "admin", "correct-horse-battery-staple") == 429


def test_unknown_usernames_are_tracked_too(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    for _ in range(5):
        assert _login_attempt(client, "ghost", "whatever-123") == 401
    assert _login_attempt(client, "ghost", "whatever-123") == 429


def test_successful_login_clears_the_failure_count(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    for _ in range(4):
        assert _login_attempt(client, "admin", "wrong-password") == 401
    assert _login_attempt(client, "admin", "correct-horse-battery-staple") == 200
    # 计数已清零：再错 4 次仍不应触发锁定。
    for _ in range(4):
        assert _login_attempt(client, "admin", "wrong-password") == 401
    assert _login_attempt(client, "admin", "wrong-password") == 401


def test_lock_expires_and_login_works_again(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_LOGIN_LOCKOUT_SECONDS", "1")

    for _ in range(5):
        assert _login_attempt(client, "admin", "wrong-password") == 401
    assert _login_attempt(client, "admin", "correct-horse-battery-staple") == 429

    deadline = time.time() + 5
    while get_runtime_store().login_lock_remaining_seconds("admin") > 0:
        assert time.time() < deadline, "login lock did not expire"
        time.sleep(0.2)
    assert _login_attempt(client, "admin", "correct-horse-battery-staple") == 200


def test_failures_and_lockouts_are_audited_without_passwords(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = {
        "Authorization": "Bearer "
        + client.post(
            "/auth/login",
            json={"username": "admin", "password": "correct-horse-battery-staple"},
        ).json()["access_token"]
    }

    for _ in range(5):
        _login_attempt(client, "admin", "wrong-password")

    events = client.get("/audit/events", headers=headers)
    assert events.status_code == 200
    login_events = [
        row
        for row in events.json().get("events", [])
        if row.get("event_type") == "auth_login"
    ]
    statuses = [row["status"] for row in login_events]
    assert statuses.count("failed") == 5
    assert statuses.count("locked") == 1
    for row in login_events:
        assert row["metadata"]["username"] == "admin"
        assert "ip" in row["metadata"]
    # 密码绝不落审计。
    assert "wrong-password" not in events.text
