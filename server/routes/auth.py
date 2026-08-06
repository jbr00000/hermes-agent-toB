from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from server import auth
from server.deps import get_current_user, require_admin

router = APIRouter(prefix="/auth", tags=["auth"])
_REFRESH_COOKIE = "hermes_refresh_token"


class Creds(BaseModel):
    username: str
    password: str


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=int(os.environ.get("HERMES_REFRESH_TOKEN_TTL", str(7 * 24 * 3600))),
        httponly=True,
        secure=os.environ.get("HERMES_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


@router.post("/login")
def login(creds: Creds, request: Request, response: Response):
    user = auth.authenticate(creds.username, creds.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    refresh_token = auth.issue_refresh_token(user, request.headers.get("user-agent"))
    _set_refresh_cookie(response, refresh_token)
    return {"access_token": auth.create_token(user), "token_type": "bearer", "user": user}


@router.post("/refresh")
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="refresh session required")
    refreshed = auth.refresh_access_token(refresh_token)
    if refreshed is None:
        response.delete_cookie(_REFRESH_COOKIE, path="/")
        raise HTTPException(status_code=401, detail="refresh session expired")
    access_token, user = refreshed
    return {"access_token": access_token, "token_type": "bearer", "user": user}


@router.get("/session")
def browser_session(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
):
    """Restore browser auth without treating an anonymous visit as an error."""
    if not refresh_token:
        return {"authenticated": False}
    refreshed = auth.refresh_access_token(refresh_token)
    if refreshed is None:
        response.delete_cookie(_REFRESH_COOKIE, path="/")
        return {"authenticated": False}
    access_token, user = refreshed
    return {
        "authenticated": True,
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
):
    if refresh_token:
        auth.revoke_refresh_token(refresh_token)
    response.delete_cookie(_REFRESH_COOKIE, path="/")


@router.post("/register", dependencies=[Depends(require_admin)])
def register(creds: Creds, request: Request, response: Response):
    try:
        user = auth.create_user(creds.username, creds.password, role="user")
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    refresh_token = auth.issue_refresh_token(user, request.headers.get("user-agent"))
    _set_refresh_cookie(response, refresh_token)
    return {"access_token": auth.create_token(user), "token_type": "bearer", "user": user}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}
