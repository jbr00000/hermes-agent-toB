"""Audit routes: GET /audit/events (admin-only read of the audit trail).

The 审计中心 page reads this to render the tenant's audit log (tool calls,
blocked SSRF attempts, failures — see docs/联网检索接入方案.md §6.3). Read-only
and admin-only: regular users never see other users' activity.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from server.deps import require_admin
from server.storage import get_repository

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


@router.get("/events")
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    event_type: str | None = None,
    user_id: str | None = None,
):
    repo = get_repository()
    rows = repo.list_audit_events(
        user_id=user_id, event_type=event_type, limit=limit, descending=True
    )
    # Resolve usernames for display (audit rows store the opaque user uuid).
    usernames: dict[str, str] = {}
    for row in rows:
        uid = row.get("user_id")
        if uid and uid not in usernames:
            user = repo.get_user(uid)
            usernames[uid] = user["username"] if user else uid
    return {
        "events": [
            {**row, "username": usernames.get(row.get("user_id") or "", "")}
            for row in rows
        ]
    }
