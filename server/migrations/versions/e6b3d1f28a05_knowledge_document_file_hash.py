"""add knowledge_documents.file_hash（上传内容查重）

上传时计算的 SHA-256，支撑库内内容级重复检测（同名检测直接比 file_name
词干，无需新列）。存量行为 NULL，只对新上传生效。SQLite 零配置路径由
server/storage/database.py 的 _ADDED_COLUMNS shim 覆盖。

Revision ID: e6b3d1f28a05
Revises: 4d8f2a6b91c0
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6b3d1f28a05"
down_revision: Union[str, Sequence[str], None] = "4d8f2a6b91c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("file_hash", sa.String(64), nullable=True))
    op.create_index(
        "idx_knowledge_docs_kb_hash",
        "knowledge_documents",
        ["kb_id", "file_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_docs_kb_hash", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "file_hash")
