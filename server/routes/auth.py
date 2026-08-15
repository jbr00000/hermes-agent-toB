from __future__ import annotations

import math
import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from server import auth
from server.deps import get_current_user
from server.storage import get_repository
from server.storage.runtime import get_runtime_store

router = APIRouter(prefix="/auth", tags=["auth"])
_REFRESH_COOKIE = "hermes_refresh_token"


class Creds(BaseModel):
    username: str
    password: str
    remember: bool = False


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


def _set_refresh_cookie(
    response: Response, token: str, max_age: int | None = None
) -> None:
    """写 refresh cookie。max_age=None → 会话 cookie（浏览器关闭即失效，
    对应登录页未勾选「保持登录」）；传入秒数 → 持久 cookie。"""
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=os.environ.get("HERMES_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


def _record_login_failure(username: str, request: Request) -> None:
    """计数 + 审计一次失败登录；达到阈值时再记一条 locked。

    审计 metadata 只含 username/IP/失败次数——绝不记录密码本身。
    """
    runtime = get_runtime_store()
    fail_count, lock_seconds = runtime.register_login_failure(username)
    client_ip = request.client.host if request.client else None
    repository = get_repository()
    repository.record_audit_event(
        event_type="auth_login",
        conversation_id=None,
        user_id=None,
        status="failed",
        mode=None,
        metadata={"username": username, "ip": client_ip, "fail_count": fail_count},
        error=None,
    )
    if lock_seconds > 0:
        repository.record_audit_event(
            event_type="auth_login",
            conversation_id=None,
            user_id=None,
            status="locked",
            mode=None,
            metadata={
                "username": username,
                "ip": client_ip,
                "fail_count": fail_count,
                "lock_seconds": lock_seconds,
            },
            error=None,
        )


@router.post("/login")
def login(creds: Creds, request: Request, response: Response):
    runtime = get_runtime_store()
    locked_for = runtime.login_lock_remaining_seconds(creds.username)
    if locked_for > 0:
        raise HTTPException(
            status_code=429,
            detail=f"账号已锁定，请 {math.ceil(locked_for / 60)} 分钟后重试",
        )
    user = auth.authenticate(creds.username, creds.password)
    if user is None:
        _record_login_failure(creds.username, request)
        raise HTTPException(status_code=401, detail="invalid username or password")
    runtime.clear_login_failures(creds.username)
    if creds.remember:
        remember_ttl = int(
            os.environ.get("HERMES_REFRESH_TOKEN_REMEMBER_TTL", str(30 * 24 * 3600))
        )
        refresh_token = auth.issue_refresh_token(
            user, request.headers.get("user-agent"), ttl=remember_ttl
        )
        _set_refresh_cookie(response, refresh_token, max_age=remember_ttl)
    else:
        # 未勾选「保持登录」：服务端会话仍按默认 TTL 兜底，cookie 不带
        # Max-Age（会话 cookie），浏览器一关即需重新登录。
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


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}


@router.post("/change-password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
):
    """用户自助改密。成功后旧 refresh 会话全部吊销，响应镜像 login（新
    refresh cookie + access_token + user），前端无需重新登录。"""
    try:
        updated = auth.change_password(user["id"], body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=400, detail="旧密码不正确")
    get_runtime_store().clear_login_failures(user["username"])
    refresh_token = auth.issue_refresh_token(updated, request.headers.get("user-agent"))
    default_ttl = int(os.environ.get("HERMES_REFRESH_TOKEN_TTL", str(7 * 24 * 3600)))
    _set_refresh_cookie(response, refresh_token, max_age=default_ttl)
    return {"access_token": auth.create_token(updated), "token_type": "bearer", "user": updated}
