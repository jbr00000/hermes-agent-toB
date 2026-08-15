"""End-to-end coverage of the superadmin-only /users API."""
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


def _create_user(
    client: TestClient, headers: dict[str, str], username: str, **overrides
) -> dict:
    payload = {"username": username, "password": "password-123", **overrides}
    response = client.post("/users", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_bootstrap_superadmin_has_full_crud(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    created = _create_user(
        client, headers, "member", role="user", features={"chat": False}
    )
    assert created["role"] == "user"
    assert created["features"]["chat"] is False
    assert created["features"]["agent"] is True

    listed = client.get("/users", headers=headers)
    assert listed.status_code == 200
    usernames = {row["username"] for row in listed.json()["users"]}
    assert usernames == {"admin", "member"}

    role = client.put(
        f"/users/{created['id']}/role", headers=headers, json={"role": "admin"}
    )
    assert role.status_code == 200

    features = client.put(
        f"/users/{created['id']}/features", headers=headers, json={"chat": True, "memory": False}
    )
    assert features.status_code == 200
    assert features.json()["features"]["chat"] is True
    assert features.json()["features"]["memory"] is False
    assert features.json()["features"]["agent"] is True  # untouched keys preserved

    status = client.put(
        f"/users/{created['id']}/status", headers=headers, json={"status": "disabled"}
    )
    assert status.status_code == 200

    deleted = client.delete(f"/users/{created['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": created["id"]}


def test_users_routes_reject_non_superadmin(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    admin = _create_user(client, super_headers, "cust-admin", role="admin")
    user = _create_user(client, super_headers, "plain", role="user")

    for account in (admin, user):
        headers = _login(client, account["username"], "password-123")
        assert client.get("/users", headers=headers).status_code == 403
        assert (
            client.post(
                "/users", headers=headers,
                json={"username": "x", "password": "password-123"},
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/users/{user['id']}/role", headers=headers, json={"role": "admin"}
            ).status_code
            == 403
        )


def test_self_lockout_guards(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    me = client.get("/auth/me", headers=headers).json()["user"]

    # Cannot delete / disable / demote yourself.
    assert client.delete(f"/users/{me['id']}", headers=headers).status_code == 400
    assert (
        client.put(
            f"/users/{me['id']}/status", headers=headers, json={"status": "disabled"}
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/users/{me['id']}/role", headers=headers, json={"role": "user"}
        ).status_code
        == 400
    )

    # Cannot delete / disable / demote the last active superadmin either.
    other = _create_user(client, headers, "boss2", role="superadmin")
    assert client.delete(f"/users/{other['id']}", headers=headers).status_code == 200
    assert (
        client.put(
            f"/users/{me['id']}/role", headers=headers, json={"role": "admin"}
        ).status_code
        == 400  # still self-demotion
    )


def test_last_superadmin_guard_via_second_actor(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers1 = _login(client, "admin", "correct-horse-battery-staple")
    boss2 = _create_user(client, headers1, "boss2", role="superadmin")
    headers2 = _login(client, "boss2", "password-123")

    me1 = client.get("/auth/me", headers=headers1).json()["user"]
    # boss2 deletes admin (the first superadmin): allowed, boss2 remains.
    assert client.delete(f"/users/{me1['id']}", headers=headers2).status_code == 200
    # Now boss2 is the last superadmin: nobody can demote them.
    assert (
        client.put(
            f"/users/{boss2['id']}/role",
            headers=headers2,
            json={"role": "admin"},
        ).status_code
        == 400  # self-demotion rule fires first
    )


def test_password_reset_revokes_old_credentials(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    member = _create_user(client, headers, "member")

    old_login = client.post(
        "/auth/login", json={"username": "member", "password": "password-123"}
    )
    assert old_login.status_code == 200
    old_refresh = old_login.cookies.get("hermes_refresh_token")
    assert old_refresh

    reset = client.put(
        f"/users/{member['id']}/password",
        headers=headers,
        json={"password": "new-password-456"},
    )
    assert reset.status_code == 200

    # Old password no longer works.
    assert (
        client.post(
            "/auth/login", json={"username": "member", "password": "password-123"}
        ).status_code
        == 401
    )
    # Old refresh session is revoked too.
    client.cookies.clear()
    client.cookies.set("hermes_refresh_token", old_refresh)
    refreshed = client.post("/auth/refresh")
    assert refreshed.status_code == 401
    client.cookies.clear()

    new_headers = _login(client, "member", "new-password-456")
    assert client.get("/auth/me", headers=new_headers).status_code == 200


def test_disabled_user_token_stops_working(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    member = _create_user(client, headers, "member")
    member_headers = _login(client, "member", "password-123")
    assert client.get("/auth/me", headers=member_headers).status_code == 200

    client.put(
        f"/users/{member['id']}/status", headers=headers, json={"status": "disabled"}
    )
    # The already-issued access token must die immediately, not at expiry.
    assert client.get("/auth/me", headers=member_headers).status_code == 401


def test_user_admin_writes_are_audited(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    member = _create_user(client, headers, "member")
    client.put(
        f"/users/{member['id']}/password",
        headers=headers,
        json={"password": "new-password-456"},
    )

    events = client.get("/audit/events", headers=headers)
    assert events.status_code == 200
    user_admin_events = [
        row for row in events.json().get("events", [])
        if row.get("event_type") == "user_admin"
    ]
    actions = {row["metadata"]["action"] for row in user_admin_events}
    assert {"create_user", "reset_password"} <= actions
    # Passwords must never appear anywhere in audit metadata.
    assert "new-password-456" not in events.text
