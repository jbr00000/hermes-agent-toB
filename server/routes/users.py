"""User-management routes (admin-only): GET/POST /users, DELETE/PUT role.

All routes require the admin role (decision 2/3: a per-customer admin manages
the 10–50 users; open self-registration is closed off — /auth/register was
open only during Inc 2 testing).
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server import auth
from server.deps import require_admin

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: Literal["user", "admin"] = "user"


class RoleUpdateRequest(BaseModel):
    role: Literal["user", "admin"]


@router.get("")
def list_users():
    return {"users": auth.list_users()}


@router.post("")
def create_user(req: CreateUserRequest):
    try:
        user = auth.create_user(req.username, req.password, role=req.role)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="username already exists")
    return {"user": user}


@router.delete("/{user_id}")
def delete_user(user_id: str):
    if not auth.delete_user(user_id):
        raise HTTPException(status_code=404, detail="user not found")
    return {"deleted": user_id}


@router.put("/{user_id}/role")
def update_role(user_id: str, req: RoleUpdateRequest):
    if not auth.set_user_role(user_id, req.role):
        raise HTTPException(status_code=404, detail="user not found")
    return {"user_id": user_id, "role": req.role}
