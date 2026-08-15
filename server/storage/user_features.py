"""Per-user feature flags shared by the auth layer and the storage layer.

Stored as a JSON column on the users table. Kept in its own module so both
``server.auth`` and ``server.storage.repository`` can import it without a
circular dependency.

Feature keys:
    agent      Agent 任务（计划/执行、沙盒跑代码）
    chat       Chat 问数对话（只读 db_query）
    knowledge  知识库查看（上传/删除仍由角色决定）
    memory     记忆页查看与管理

Semantics: a missing column (NULL), a missing key, or an unknown shape all
normalize to *enabled* — existing users must not lose access after upgrade.
"""
from __future__ import annotations

from typing import Any

FEATURE_KEYS: tuple[str, ...] = ("agent", "chat", "knowledge", "memory")

DEFAULT_USER_FEATURES: dict[str, bool] = {key: True for key in FEATURE_KEYS}


def normalize_features(raw: Any) -> dict[str, bool]:
    """Merge a stored/patched features dict over the all-enabled default."""
    merged = dict(DEFAULT_USER_FEATURES)
    if isinstance(raw, dict):
        for key in FEATURE_KEYS:
            if key in raw:
                merged[key] = bool(raw[key])
    return merged


def user_features(user: dict[str, Any]) -> dict[str, bool]:
    """Return the effective feature flags for a user dict from the repository."""
    return normalize_features(user.get("features"))
