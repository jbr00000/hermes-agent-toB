"""FastAPI auth dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server import auth

_bearer = HTTPBearer()

# 强制改密用户在改密完成前只允许访问这些端点（其余一律 403）。
_MUST_CHANGE_PASSWORD_ALLOWED_PATHS = frozenset(
    {"/auth/change-password", "/auth/me", "/auth/session", "/auth/logout"}
)


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Resolve the JWT in the Authorization: Bearer header to a user dict.

    Raises 401 on missing/expired token, unknown user, or a disabled account.
    The user row is re-read from storage on every request, so role/status/
    feature changes take effect immediately (JWT claims are only an identity
    pointer, not a permission snapshot).

    A user flagged must_change_password is additionally confined to the
    password-change whitelist: every other endpoint returns 403
    (detail="must_change_password") until the flag is cleared.
    """
    payload = auth.decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = auth.get_user(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    if user.get("status") != "active":
        raise HTTPException(status_code=401, detail="account disabled")
    if (
        user.get("must_change_password")
        and request.url.path not in _MUST_CHANGE_PASSWORD_ALLOWED_PATHS
    ):
        raise HTTPException(status_code=403, detail="must_change_password")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: the authenticated user must be admin or superadmin, else 403.

    superadmin is a superset of admin: anything an admin can reach (audit,
    knowledge management), a superadmin can reach too.
    """
    if user.get("role") not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


async def require_superadmin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: the authenticated user must have role=='superadmin', else 403."""
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="superadmin role required")
    return user


def require_feature(name: str):
    """Dependency factory: the user's per-feature flags must include ``name``.

    Feature flags live on the user row (users.features JSON); see
    server/storage/user_features.py. Unknown flags default to enabled.
    """

    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if not auth.user_features(user).get(name, True):
            raise HTTPException(
                status_code=403, detail=f"feature '{name}' is disabled for this user"
            )
        return user

    return _dep
