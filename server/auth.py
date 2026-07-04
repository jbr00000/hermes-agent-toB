"""Authentication: users.db (SQLite) + bcrypt password hashing + JWT.

Inc 2 of the headless server. Every authenticated request resolves a user_id
that scopes the AIAgent session (and, in Inc 3, the SessionDB rows) — this is
the per-user isolation boundary required by decision 2/3 of the transformation
plan (single-tenant deploy, shared instance, 10–50 users must not cross).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
import uuid
from typing import Optional

import bcrypt
import jwt

from hermes_constants import get_hermes_home


def _hash_pw(password: str) -> str:
    """Bcrypt hash (utf-8 encoded; bcrypt's 72-byte limit applies)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pw(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

_DB_PATH: Optional[str] = None
_JWT_SECRET: Optional[str] = None
_JWT_ALG = "HS256"
_JWT_TTL_SECONDS = 24 * 3600


def _db_path() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = str(get_hermes_home() / "users.db")
    return _DB_PATH


def _jwt_secret() -> str:
    global _JWT_SECRET
    if _JWT_SECRET is None:
        key_file = get_hermes_home() / "jwt.key"
        if key_file.exists():
            _JWT_SECRET = key_file.read_text(encoding="utf-8").strip()
        else:
            _JWT_SECRET = secrets.token_urlsafe(48)
            key_file.write_text(_JWT_SECRET, encoding="utf-8")
    return _JWT_SECRET


def init_db() -> None:
    """Create the users table if missing and bootstrap an admin on first run."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        con.commit()
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            username = os.environ.get("HERMES_ADMIN_USERNAME", "admin")
            password = os.environ.get("HERMES_ADMIN_PASSWORD", "changeme")
            con.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at) VALUES(?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), username, _hash_pw(password), "admin", time.time()),
            )
            con.commit()
            if password == "changeme":
                print(
                    f"[auth] WARNING: bootstrapped admin '{username}' with default "
                    "password 'changeme' — set HERMES_ADMIN_PASSWORD for any non-dev deploy."
                )
            else:
                print(f"[auth] bootstrapped admin user '{username}'.")
    finally:
        con.close()


def create_user(username: str, password: str, role: str = "user") -> dict:
    con = sqlite3.connect(_db_path())
    try:
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "role": role,
            "created_at": time.time(),
        }
        con.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) VALUES(?, ?, ?, ?, ?)",
            (user["id"], username, _hash_pw(password), role, user["created_at"]),
        )
        con.commit()
        return user
    finally:
        con.close()


def authenticate(username: str, password: str) -> Optional[dict]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None or not _verify_pw(password, row[2]):
            return None
        return {"id": row[0], "username": row[1], "role": row[3]}
    finally:
        con.close()


def get_user(user_id: str) -> Optional[dict]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "role": row[2]}
    finally:
        con.close()


def create_token(user: dict) -> str:
    now = int(time.time())
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + _JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALG)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALG])
    except Exception:
        return None
