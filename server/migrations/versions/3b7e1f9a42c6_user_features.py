"""add users.features column (per-user feature flags)

superadmin 三级角色 + 用户级功能开关（agent/chat/knowledge/memory）。
NULL = 全开（读取侧归一化兜底），这里同时显式回填存量行。

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


def downgrade() -> None:
    op.drop_column("users", "features")
