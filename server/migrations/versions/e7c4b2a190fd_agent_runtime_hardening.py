"""harden Agent runtime state and event metadata

Revision ID: e7c4b2a190fd
Revises: bd2bb9639fe4
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7c4b2a190fd"
down_revision: Union[str, Sequence[str], None] = "bd2bb9639fe4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks", sa.Column("source_session_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "task_runs", sa.Column("cancel_requested_at", sa.Double(), nullable=True)
    )
    op.add_column(
        "tool_events",
        sa.Column(
            "risk_level",
            sa.String(length=24),
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    op.drop_column("tool_events", "risk_level")
    op.drop_column("task_runs", "cancel_requested_at")
    op.drop_column("agent_tasks", "source_session_id")
