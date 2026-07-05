from __future__ import annotations

import pytest

from server import auth


@pytest.fixture(autouse=True)
def _reset_auth_state(monkeypatch):
    monkeypatch.setattr(auth, "_DB_PATH", None)
    monkeypatch.setattr(auth, "_JWT_SECRET", None)
    monkeypatch.delenv("HERMES_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_ALLOW_DEFAULT_ADMIN", raising=False)


def test_init_db_rejects_default_admin_without_explicit_dev_override() -> None:
    with pytest.raises(RuntimeError, match="HERMES_ADMIN_PASSWORD"):
        auth.init_db()


def test_init_db_allows_default_admin_when_dev_override_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ALLOW_DEFAULT_ADMIN", "1")

    auth.init_db()

    user = auth.authenticate("admin", "changeme")
    assert user is not None
    assert user["role"] == "admin"


def test_init_db_bootstraps_admin_with_custom_password(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")

    auth.init_db()

    assert auth.authenticate("owner", "changeme") is None
    user = auth.authenticate("owner", "correct-horse-battery-staple")
    assert user is not None
    assert user["role"] == "admin"


def test_create_user_rejects_unknown_role(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    auth.init_db()

    with pytest.raises(ValueError, match="invalid role"):
        auth.create_user("bad-role", "password", role="owner")


def test_set_user_role_rejects_unknown_role(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    auth.init_db()
    user = auth.create_user("member", "password")

    with pytest.raises(ValueError, match="invalid role"):
        auth.set_user_role(user["id"], "owner")
