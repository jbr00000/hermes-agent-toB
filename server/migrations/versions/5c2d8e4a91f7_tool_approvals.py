"""add tool_approvals table (controlled 权限档运行中途审批)

controlled 模式：agent 拿到 terminal 工具集，每条 terminal/process 命令
需用户在 Web 上批准后执行；每条命令一行审批记录。

Revision ID: 5c2d8e4a91f7
Revises: 3b7e1f9a42c6
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5c2d8e4a91f7"
down_revision: Union[str, Sequence[str], None] = "3b7e1f9a42c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_approvals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_request_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("command_preview", sa.String(length=512), nullable=False),
        sa.Column("args_fingerprint", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("decided_at", sa.Double(), nullable=True),
    )
    op.create_index(
        "idx_tool_approvals_task_status",
        "tool_approvals",
        ["tenant_id", "task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_tool_approvals_task_status", table_name="tool_approvals")
    op.drop_table("tool_approvals")
