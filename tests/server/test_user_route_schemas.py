from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.routes.users import (
    CreateUserRequest,
    PasswordResetRequest,
    RoleUpdateRequest,
    StatusUpdateRequest,
    UserFeaturesIn,
)


def test_create_user_request_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username="bad-role", password="password", role="owner")


def test_create_user_request_accepts_superadmin_role() -> None:
    req = CreateUserRequest(username="boss", password="password-123", role="superadmin")
    assert req.role == "superadmin"


def test_create_user_request_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username="short-pw", password="abc123")


def test_password_reset_request_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        PasswordResetRequest(password="abc123")


def test_role_update_request_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        RoleUpdateRequest(role="owner")


def test_status_update_request_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        StatusUpdateRequest(status="banned")


def test_user_features_in_is_patch_style() -> None:
    patch = UserFeaturesIn(chat=False)
    assert patch.model_dump(exclude_none=True) == {"chat": False}
    assert UserFeaturesIn().model_dump(exclude_none=True) == {}
