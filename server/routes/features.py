"""Feature-flag routes: GET /features (read current flags).

The frontend reads this to render opt-in feature state such as host_terminal.
Toggling (POST) lands in Inc 2 once the
admin-only write path + audit logging are added.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from server import features
from server.deps import get_current_user

router = APIRouter(prefix="/features", tags=["features"])


@router.get("")
def get_all(user: dict = Depends(get_current_user)):
    return {"features": features.get_features()}
