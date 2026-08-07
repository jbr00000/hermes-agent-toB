"""add agent task schema

Revision ID: f42d85d31c70
Revises: c71a4d90be32
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f42d85d31c70"
down_revision: Union[str, Sequence[str], None] = "c71a4d90be32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("current_run_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.Column("completed_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_agent_tasks_conversation"),
    )
    op.create_index(
        "idx_agent_tasks_owner_updated",
        "agent_tasks",
        ["tenant_id", "user_id", "updated_at"],
    )

    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Double(), nullable=False),
        sa.Column("completed_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_task_runs_task_started", "task_runs", ["tenant_id", "task_id", "started_at"]
    )

    op.create_table(
        "task_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("approved_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version", name="uq_task_plans_task_version"),
    )
    op.create_index(
        "idx_task_plans_task_created", "task_plans", ["tenant_id", "task_id", "created_at"]
    )

    op.create_table(
        "permission_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("expires_at", sa.Double(), nullable=False),
        sa.Column("revoked_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_permission_leases_task_active",
        "permission_leases",
        ["tenant_id", "task_id", "expires_at"],
    )

    op.create_table(
        "tool_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_tool_events_run_sequence"),
    )
    op.create_index(
        "idx_tool_events_task_created", "tool_events", ["tenant_id", "task_id", "created_at"]
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("expires_at", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_artifacts_task_created", "artifacts", ["tenant_id", "task_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_artifacts_task_created", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("idx_tool_events_task_created", table_name="tool_events")
    op.drop_table("tool_events")
    op.drop_index("idx_permission_leases_task_active", table_name="permission_leases")
    op.drop_table("permission_leases")
    op.drop_index("idx_task_plans_task_created", table_name="task_plans")
    op.drop_table("task_plans")
    op.drop_index("idx_task_runs_task_started", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("idx_agent_tasks_owner_updated", table_name="agent_tasks")
    op.drop_table("agent_tasks")
