"""add model run metadata to messages

Revision ID: 8d64c1ef3a20
Revises: 3b091f7639bf
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d64c1ef3a20"
down_revision: Union[str, Sequence[str], None] = "3b091f7639bf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("model_run_id", sa.String(length=64), nullable=True))
    op.add_column("messages", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_messages_model_run_id"), "messages", ["model_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_model_run_id"), table_name="messages")
    op.drop_column("messages", "duration_ms")
    op.drop_column("messages", "model_run_id")
