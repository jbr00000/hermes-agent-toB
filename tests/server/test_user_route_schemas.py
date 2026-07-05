from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.routes.users import CreateUserRequest, RoleUpdateRequest


def test_create_user_request_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username="bad-role", password="password", role="owner")


def test_role_update_request_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        RoleUpdateRequest(role="owner")
