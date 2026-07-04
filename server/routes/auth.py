"""Auth routes: POST /auth/login, POST /auth/register, GET /auth/me.

Inc 2: registration is open so a second user can be created for isolation
testing. Inc 5 restricts /auth/register to admin-only (to-B user management).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server import auth
from server.deps import get_current_user, require_admin

router = APIRouter(prefix="/auth", tags=["auth"])


class Creds(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(creds: Creds):
    user = auth.authenticate(creds.username, creds.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"access_token": auth.create_token(user), "token_type": "bearer", "user": user}


@router.post("/register", dependencies=[Depends(require_admin)])
def register(creds: Creds):
    """Admin-only user creation (open self-registration closed in Inc 5)."""
    try:
        user = auth.create_user(creds.username, creds.password, role="user")
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="username already exists")
    return {"access_token": auth.create_token(user), "token_type": "bearer", "user": user}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}
