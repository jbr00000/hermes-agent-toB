"""add users.features column (per-user feature flags)

superadmin 三级角色 + 用户级功能开关（agent/chat/knowledge/memory）。
NULL = 全开（读取侧归一化兜底），这里同时显式回填存量行。
另含升级路径 data migration：superadmin 角色引入前的部署只有 admin/user
行，把最老的 active admin 提为 superadmin，保证用户管理面可达（客户可随后
自建 superadmin 并降级该账号）。SQLite 零配置路径由
server/storage/database.py::_ensure_superadmin 做等价处理。

Revision ID: 3b7e1f9a42c6
Revises: 1c9e4a7b2d03
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3b7e1f9a42c6"
down_revision: Union[str, Sequence[str], None] = "1c9e4a7b2d03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_FEATURES = {"agent": True, "chat": True, "knowledge": True, "memory": True}


def upgrade() -> None:
    op.add_column("users", sa.Column("features", sa.JSON(), nullable=True))
    users = sa.table("users", sa.column("features", sa.JSON))
    op.execute(
        users.update().where(users.c.features.is_(None)).values(features=_ALL_FEATURES)
    )
    _promote_oldest_admin_to_superadmin()


def _promote_oldest_admin_to_superadmin() -> None:
    """无 active superadmin 时提升最老的 active admin。幂等。"""
    bind = op.get_bind()
    has_superadmin = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'superadmin' AND status = 'active'")
    ).scalar()
    if has_superadmin:
        return
    oldest = bind.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' AND status = 'active' "
            "ORDER BY created_at ASC LIMIT 1"
        )
    ).scalar()
    if oldest is None:
        return
    bind.execute(
        sa.text("UPDATE users SET role = 'superadmin' WHERE id = :id"), {"id": oldest}
    )


def downgrade() -> None:
    op.drop_column("users", "features")
