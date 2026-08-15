"""User-management routes (superadmin-only).

Three-tier role model: superadmin (delivery side) manages all users; admin
(customer side) keeps audit/knowledge management but no longer manages users;
user is pure business usage. Open self-registration is closed off — accounts
are created here by a superadmin only.

Every write operation is recorded as a ``user_admin`` audit event (never
including the password itself).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from server import auth
from server.deps import require_superadmin
from server.storage import get_repository

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_superadmin)])


class UserFeaturesIn(BaseModel):
    """Patch-style feature input: omitted keys keep the default (enabled)."""

    agent: bool | None = None
    chat: bool | None = None
    knowledge: bool | None = None
    memory: bool | None = None


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=auth.MIN_PASSWORD_LENGTH, max_length=256)
    role: Literal["superadmin", "admin", "user"] = "user"
    features: UserFeaturesIn | None = None  # default: all enabled


class RoleUpdateRequest(BaseModel):
    role: Literal["superadmin", "admin", "user"]


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=auth.MIN_PASSWORD_LENGTH, max_length=256)


class StatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]


def _get_target_or_404(user_id: str) -> dict:
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    return target


def _guard_superadmin_target(actor: dict, target: dict, action: str) -> None:
    """Anti-lockout rules for destructive actions on superadmin accounts.

    action ∈ {"delete", "disable", "demote"}: you cannot do it to yourself,
    and you cannot do it to the last active superadmin.
    """
    if target["id"] == actor["id"]:
        raise HTTPException(status_code=400, detail=f"cannot {action} your own account")
    if target["role"] == "superadmin":
        if get_repository().count_active_superadmins() <= 1:
            raise HTTPException(
                status_code=409, detail="at least one active superadmin is required"
            )


def _audit(actor: dict, action: str, target: dict, **detail: object) -> None:
    get_repository().record_audit_event(
        event_type="user_admin",
        conversation_id=None,
        user_id=actor["id"],
        status="completed",
        mode=None,
        metadata={
            "action": action,
            "target_user_id": target["id"],
            "target_username": target["username"],
            **detail,
        },
        error=None,
    )


@router.get("")
def list_users():
    return {"users": auth.list_users()}


@router.post("")
def create_user(req: CreateUserRequest, actor: dict = Depends(require_superadmin)):
    try:
        user = auth.create_user(
            req.username,
            req.password,
            role=req.role,
            features=req.features.model_dump(exclude_none=True) if req.features else None,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "create_user", user, role=user["role"], features=user["features"])
    return {"user": user}


@router.delete("/{user_id}")
def delete_user(user_id: str, actor: dict = Depends(require_superadmin)):
    target = _get_target_or_404(user_id)
    _guard_superadmin_target(actor, target, "delete")
    auth.delete_user(user_id)
    _audit(actor, "delete_user", target)
    return {"deleted": user_id}


@router.put("/{user_id}/role")
def update_role(user_id: str, req: RoleUpdateRequest, actor: dict = Depends(require_superadmin)):
    target = _get_target_or_404(user_id)
    if target["role"] == "superadmin" and req.role != "superadmin":
        _guard_superadmin_target(actor, target, "demote")
    try:
        auth.set_user_role(user_id, req.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "set_role", target, role=req.role)
    return {"user_id": user_id, "role": req.role}


@router.put("/{user_id}/password")
def reset_password(
    user_id: str, req: PasswordResetRequest, actor: dict = Depends(require_superadmin)
):
    target = _get_target_or_404(user_id)
    try:
        auth.set_user_password(user_id, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Password is deliberately NOT included in the audit metadata.
    _audit(actor, "reset_password", target)
    return {"user_id": user_id, "password_reset": True}


@router.put("/{user_id}/status")
def update_status(
    user_id: str, req: StatusUpdateRequest, actor: dict = Depends(require_superadmin)
):
    target = _get_target_or_404(user_id)
    if req.status != "active":
        _guard_superadmin_target(actor, target, "disable")
    auth.set_user_status(user_id, req.status)
    _audit(actor, "set_status", target, status=req.status)
    return {"user_id": user_id, "status": req.status}


@router.put("/{user_id}/features")
def update_features(
    user_id: str, req: UserFeaturesIn, actor: dict = Depends(require_superadmin)
):
    target = _get_target_or_404(user_id)
    merged = {**target["features"], **req.model_dump(exclude_none=True)}
    auth.set_user_features(user_id, merged)
    updated = auth.get_user(user_id)
    _audit(actor, "set_features", target, features=updated["features"])
    return {"user_id": user_id, "features": updated["features"]}
