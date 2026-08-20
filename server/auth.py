"""Authentication backed by the shared to-B storage repository."""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any, Optional

import bcrypt
import jwt

from hermes_constants import get_hermes_home
from server.storage import get_repository, init_storage
from server.storage.user_features import (
    DEFAULT_USER_FEATURES,
    normalize_features,
    user_features,
)

_DB_PATH: Optional[str] = None  # Backward-compatible test reset hook.
_JWT_SECRET: Optional[str] = None
_JWT_ALG = "HS256"
VALID_ROLES = frozenset({"superadmin", "admin", "user"})
VALID_STATUSES = frozenset({"active", "disabled"})
MIN_PASSWORD_LENGTH = 8


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pw(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_role(role: str) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role {role!r}; expected one of: {', '.join(sorted(VALID_ROLES))}")
    return role


def _validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def _validate_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of: {', '.join(sorted(VALID_STATUSES))}")
    return status


def _jwt_secret() -> str:
    global _JWT_SECRET
    configured = os.environ.get("HERMES_JWT_SECRET", "").strip()
    if configured:
        return configured
    if _JWT_SECRET is None:
        key_file = get_hermes_home() / "jwt.key"
        if key_file.exists():
            _JWT_SECRET = key_file.read_text(encoding="utf-8").strip()
        else:
            _JWT_SECRET = secrets.token_urlsafe(48)
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(_JWT_SECRET, encoding="utf-8")
    return _JWT_SECRET


def init_db() -> None:
    init_storage()
    repository = get_repository()
    if repository.count_users() > 0:
        return
    username = os.environ.get("HERMES_ADMIN_USERNAME", "admin")
    password = os.environ.get("HERMES_ADMIN_PASSWORD") or "changeme"
    if not password:
        raise RuntimeError("HERMES_ADMIN_PASSWORD must not be empty")
    if password == "changeme" and not _env_flag("HERMES_ALLOW_DEFAULT_ADMIN"):
        raise RuntimeError(
            "Refusing to bootstrap admin with the default password. "
            "Set HERMES_ADMIN_PASSWORD, or set HERMES_ALLOW_DEFAULT_ADMIN=1 "
            "only for local development."
        )
    repository.create_user(
        username, _hash_pw(password), "superadmin",
        features=dict(DEFAULT_USER_FEATURES),
    )
    if password == "changeme":
        print(f"[auth] WARNING: bootstrapped superadmin '{username}' with default password 'changeme'.")
    else:
        print(f"[auth] bootstrapped superadmin user '{username}'.")


def create_user(
    username: str,
    password: str,
    role: str = "user",
    features: dict[str, Any] | None = None,
) -> dict:
    role = _validate_role(role)
    _validate_password(password)
    normalized_username = username.strip()
    if not normalized_username:
        raise ValueError("username must not be empty")
    # 管理员代建的账号用初始密码，首次登录必须改密。
    return get_repository().create_user(
        normalized_username, _hash_pw(password), role,
        features=normalize_features(features),
        must_change_password=True,
    )


def authenticate(username: str, password: str) -> Optional[dict]:
    row = get_repository().get_user_by_username(username.strip(), include_password=True)
    if row is None or row.get("status") != "active":
        return None
    if not _verify_pw(password, row.pop("password_hash")):
        return None
    return row


def get_user(user_id: str) -> Optional[dict]:
    return get_repository().get_user(user_id)


def list_users() -> list:
    return get_repository().list_users()


def delete_user(user_id: str) -> bool:
    return get_repository().delete_user(user_id)


def set_user_role(user_id: str, role: str) -> bool:
    return get_repository().set_user_role(user_id, _validate_role(role))


def set_user_password(user_id: str, password: str) -> bool:
    """Reset a user's password and revoke all their live refresh sessions.

    Admin-initiated reset: the new password is a temporary one, so the user is
    forced to choose their own at next login."""
    _validate_password(password)
    repository = get_repository()
    updated = repository.set_user_password(
        user_id, _hash_pw(password), must_change_password=True
    )
    if updated:
        repository.revoke_auth_sessions_for_user(user_id)
    return updated


def change_password(user_id: str, old_password: str, new_password: str) -> Optional[dict]:
    """User-initiated password change. Verifies the old password, clears the
    must_change_password flag, and revokes all live refresh sessions (the
    caller issues a fresh one). Returns the updated user, or None if the old
    password is wrong."""
    _validate_password(new_password)
    repository = get_repository()
    row = repository.get_user(user_id, include_password=True)
    if row is None or not _verify_pw(old_password, row["password_hash"]):
        return None
    repository.set_user_password(user_id, _hash_pw(new_password), must_change_password=False)
    repository.revoke_auth_sessions_for_user(user_id)
    return repository.get_user(user_id)


def set_user_status(user_id: str, status: str) -> bool:
    """Enable/disable a user. Disabling also revokes live refresh sessions."""
    _validate_status(status)
    repository = get_repository()
    updated = repository.set_user_status(user_id, status)
    if updated and status != "active":
        repository.revoke_auth_sessions_for_user(user_id)
    return updated


def set_user_features(user_id: str, features: dict[str, Any]) -> bool:
    return get_repository().set_user_features(user_id, normalize_features(features))


def create_token(user: dict) -> str:
    now = int(time.time())
    ttl = int(os.environ.get("HERMES_ACCESS_TOKEN_TTL", "900"))
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALG)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALG])
    except Exception:
        return None


def issue_refresh_token(
    user: dict, user_agent: str | None = None, *, ttl: int | None = None
) -> str:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if ttl is None:
        ttl = int(os.environ.get("HERMES_REFRESH_TOKEN_TTL", str(7 * 24 * 3600)))
    get_repository().create_auth_session(
        user["id"], token_hash, time.time() + ttl, user_agent
    )
    return token


def refresh_access_token(refresh_token: str) -> tuple[str, dict] | None:
    token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    auth_session = get_repository().get_auth_session(token_hash)
    if auth_session is None:
        return None
    user = get_user(auth_session["user_id"])
    if user is None or user.get("status") != "active":
        return None
    return create_token(user), user


def revoke_refresh_token(refresh_token: str) -> bool:
    token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    return get_repository().revoke_auth_session(token_hash)
