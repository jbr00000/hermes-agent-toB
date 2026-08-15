"""add users.must_change_password (强制改密)

建号 / 管理员重置密码后置 True，用户登录后必须先改密才能进入工作台。

Revision ID: 7e3f1a0c92b8
Revises: 5c2d8e4a91f7
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7e3f1a0c92b8"
down_revision: Union[str, Sequence[str], None] = "5c2d8e4a91f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
