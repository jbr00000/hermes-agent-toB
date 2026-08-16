"""add messages.metadata_json（知识库问答引用等结构化附件）

assistant 消息的可选 JSON 附件：目前用于 knowledge 模式落 citations，
让刷新/重开会话后引用卡片仍可渲染。SQLite 零配置路径由
server/storage/database.py 的 _ADDED_COLUMNS shim 覆盖。

Revision ID: 4d8f2a6b91c0
Revises: 9c4d2f8a15e7
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d8f2a6b91c0"
down_revision: Union[str, Sequence[str], None] = "9c4d2f8a15e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("metadata_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "metadata_json")
