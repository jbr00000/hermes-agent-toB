"""use precise epoch timestamps

Revision ID: c71a4d90be32
Revises: 8d64c1ef3a20
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c71a4d90be32"
down_revision: Union[str, Sequence[str], None] = "8d64c1ef3a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TIMESTAMP_COLUMNS = {
    "users": (("created_at", False), ("updated_at", False)),
    "auth_sessions": (
        ("created_at", False),
        ("expires_at", False),
        ("revoked_at", True),
    ),
    "conversations": (
        ("approved_at", True),
        ("created_at", False),
        ("updated_at", False),
        ("ended_at", True),
    ),
    "messages": (("created_at", False),),
    "model_runs": (("started_at", False), ("completed_at", True)),
    "memory_items": (("created_at", False),),
    "memory_candidates": (("created_at", False), ("decided_at", True)),
    "audit_events": (("created_at", False),),
}


def _alter_timestamps(source_type: sa.types.TypeEngine, target_type: sa.types.TypeEngine) -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table, columns in TIMESTAMP_COLUMNS.items():
            with op.batch_alter_table(table) as batch_op:
                for column, nullable in columns:
                    batch_op.alter_column(
                        column,
                        existing_type=source_type,
                        type_=target_type,
                        existing_nullable=nullable,
                    )
        return
    for table, columns in TIMESTAMP_COLUMNS.items():
        for column, nullable in columns:
            op.alter_column(
                table,
                column,
                existing_type=source_type,
                type_=target_type,
                existing_nullable=nullable,
            )


def upgrade() -> None:
    _alter_timestamps(sa.Float(), sa.Double())


def downgrade() -> None:
    _alter_timestamps(sa.Double(), sa.Float())
