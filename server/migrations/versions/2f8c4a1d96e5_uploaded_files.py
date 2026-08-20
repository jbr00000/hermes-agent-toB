"""add uploaded_files (chat/agent 临时附件：只解析不分块，随 owner 删除)

Revision ID: 2f8c4a1d96e5
Revises: b5e9d2f47c31
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f8c4a1d96e5"
down_revision: Union[str, Sequence[str], None] = "b5e9d2f47c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_ext", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("parsed_path", sa.String(length=500), nullable=True),
        sa.Column("parse_status", sa.String(length=16), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parser", sa.String(length=32), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
    )
    op.create_index(
        "idx_uploaded_files_owner",
        "uploaded_files",
        ["tenant_id", "owner_type", "owner_id"],
    )
    op.create_index(
        "idx_uploaded_files_user",
        "uploaded_files",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_uploaded_files_user", table_name="uploaded_files")
    op.drop_index("idx_uploaded_files_owner", table_name="uploaded_files")
    op.drop_table("uploaded_files")
