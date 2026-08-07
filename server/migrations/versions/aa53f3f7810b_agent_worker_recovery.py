"""add durable Agent worker recovery metadata

Revision ID: aa53f3f7810b
Revises: f42d85d31c70
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aa53f3f7810b"
down_revision: Union[str, Sequence[str], None] = "f42d85d31c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("request_payload", sa.JSON(), nullable=True))
    op.add_column("task_runs", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.add_column("task_runs", sa.Column("heartbeat_at", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "heartbeat_at")
    op.drop_column("task_runs", "worker_id")
    op.drop_column("task_runs", "request_payload")
