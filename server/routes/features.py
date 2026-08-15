"""Feature-flag routes: GET /features (read current flags).

The frontend reads this to render opt-in feature state such as host_terminal.
Toggling (POST) lands in Inc 2 once the
admin-only write path + audit logging are added.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from server import features
from server.data_permissions import allowed_tables_for_role
from server.deps import get_current_user

router = APIRouter(prefix="/features", tags=["features"])


@router.get("")
def get_all(user: dict = Depends(get_current_user)):
    allowed_tables = allowed_tables_for_role(str(user.get("role") or ""))
    return {
        "features": features.get_features(),
        # None → 该角色不限制表访问；列表（可为空）→ 白名单。
        "data_permissions": {
            "enabled": allowed_tables is not None,
            "allowed_tables": allowed_tables,
        },
    }
