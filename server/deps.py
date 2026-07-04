"""FastAPI auth dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server import auth

_bearer = HTTPBearer()


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Resolve the JWT in the Authorization: Bearer header to a user dict.

    Raises 401 on missing/expired token or unknown user.
    """
    payload = auth.decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = auth.get_user(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: the authenticated user must have role=='admin', else 403."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user
