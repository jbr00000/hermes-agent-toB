"""add knowledge_bases + kb_id columns (三步流程：建库/上传/选文档解析)

知识库从"单一隐式库、上传即解析"改为显式三步：① 新建知识库（多库实体）
② 上传文档（status=uploaded，不入队）③ 勾选文档批量解析。存量文档按
tenant 回填进"默认知识库"；chunks 冗余 kb_id 为后续按库检索预留。

Revision ID: 9c4d2f8a15e7
Revises: 7e3f1a0c92b8
Create Date: 2026-08-15 00:00:00.000000

"""
import time
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c4d2f8a15e7"
down_revision: Union[str, Sequence[str], None] = "7e3f1a0c92b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_KB_NAME = "默认知识库"


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.String(length=36), nullable=True),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
    )
    op.create_index(
        "idx_knowledge_bases_tenant_updated",
        "knowledge_bases",
        ["tenant_id", "updated_at"],
    )

    # 先加可空列 → 数据回填 → 改 NOT NULL（回填必须发生在收紧约束之前）。
    op.add_column("knowledge_documents", sa.Column("kb_id", sa.String(length=36), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("kb_id", sa.String(length=36), nullable=True))

    conn = op.get_bind()
    now = time.time()
    tenants = conn.execute(
        sa.text("SELECT DISTINCT tenant_id FROM knowledge_documents")
    ).fetchall()
    for (tenant_id,) in tenants:
        kb_id = uuid.uuid4().hex
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, description, creator_id, doc_count, chunk_count, "
                " created_at, updated_at) "
                "VALUES (:id, :t, :n, NULL, NULL, 0, 0, :now, :now)"
            ),
            {"id": kb_id, "t": tenant_id, "n": _DEFAULT_KB_NAME, "now": now},
        )
        conn.execute(
            sa.text("UPDATE knowledge_documents SET kb_id = :kb WHERE tenant_id = :t"),
            {"kb": kb_id, "t": tenant_id},
        )
        conn.execute(
            sa.text(
                "UPDATE knowledge_chunks SET kb_id = :kb WHERE tenant_id = :t"
            ),
            {"kb": kb_id, "t": tenant_id},
        )
        # 计数与现状对齐（repository 之后增量维护）。
        conn.execute(
            sa.text(
                "UPDATE knowledge_bases SET "
                "doc_count = (SELECT COUNT(*) FROM knowledge_documents d "
                "             WHERE d.kb_id = :kb), "
                "chunk_count = (SELECT COUNT(*) FROM knowledge_chunks c "
                "               WHERE c.kb_id = :kb) "
                "WHERE id = :kb"
            ),
            {"kb": kb_id},
        )

    op.alter_column(
        "knowledge_documents", "kb_id", existing_type=sa.String(length=36), nullable=False
    )
    op.alter_column(
        "knowledge_chunks", "kb_id", existing_type=sa.String(length=36), nullable=False
    )
    op.create_index("idx_knowledge_docs_kb", "knowledge_documents", ["kb_id"])
    op.create_index("idx_knowledge_chunks_kb", "knowledge_chunks", ["kb_id"])


def downgrade() -> None:
    op.drop_index("idx_knowledge_chunks_kb", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_docs_kb", table_name="knowledge_documents")
    op.drop_column("knowledge_chunks", "kb_id")
    op.drop_column("knowledge_documents", "kb_id")
    op.drop_index("idx_knowledge_bases_tenant_updated", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
