"""登录页「保持登录」(remember-me) 测试。

勾选 remember → 持久 cookie(Max-Age=30 天)且服务端 auth_sessions 按 30 天过期;
不勾选 → 会话 cookie(无 Max-Age),服务端会话仍按默认 7 天 TTL 兜底。
"""
from __future__ import annotations

import hashlib
import time

from fastapi.testclient import TestClient


_ADMIN_PASSWORD = "correct-horse-battery-staple"
_REFRESH_COOKIE = "hermes_refresh_token"


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", _ADMIN_PASSWORD)
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    monkeypatch.delenv("HERMES_REFRESH_TOKEN_TTL", raising=False)
    monkeypatch.delenv("HERMES_REFRESH_TOKEN_REMEMBER_TTL", raising=False)

    from server import auth
    from server.storage import reset_storage_for_tests
    from server.storage.runtime import reset_runtime_store_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()
    reset_runtime_store_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, **payload) -> object:
    body = {"username": "admin", "password": _ADMIN_PASSWORD}
    body.update(payload)
    response = client.post("/auth/login", json=body)
    assert response.status_code == 200, response.text
    return response


def _session_expires_at(response) -> float:
    token = response.cookies.get(_REFRESH_COOKIE)
    assert token
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    from server.storage import get_repository

    auth_session = get_repository().get_auth_session(token_hash)
    assert auth_session is not None
    return auth_session["expires_at"]


def test_remember_login_sets_persistent_cookie_and_long_ttl(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = _login(client, remember=True)

    set_cookie = response.headers["set-cookie"]
    assert f"Max-Age={30 * 24 * 3600}" in set_cookie

    expires_at = _session_expires_at(response)
    assert abs(expires_at - (time.time() + 30 * 24 * 3600)) < 60


def test_default_login_sets_session_cookie_with_default_ttl(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = _login(client)

    set_cookie = response.headers["set-cookie"]
    assert "Max-Age" not in set_cookie

    # 服务端会话仍按默认 7 天 TTL 兜底。
    expires_at = _session_expires_at(response)
    assert abs(expires_at - (time.time() + 7 * 24 * 3600)) < 60


def test_remember_ttl_env_override(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    # env 在请求时才读取，client 建好后再覆盖（_client 里会 delenv 清场）。
    monkeypatch.setenv("HERMES_REFRESH_TOKEN_REMEMBER_TTL", "3600")
    response = _login(client, remember=True)

    assert "Max-Age=3600" in response.headers["set-cookie"]
    expires_at = _session_expires_at(response)
    assert abs(expires_at - (time.time() + 3600)) < 60


def test_both_modes_return_access_token(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    for payload in ({}, {"remember": True}, {"remember": False}):
        response = _login(client, **payload)
        assert response.json()["access_token"]
        assert response.json()["user"]["username"] == "admin"
