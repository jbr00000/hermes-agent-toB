"""add enterprise knowledge base tables

knowledge_documents / knowledge_chunks / knowledge_jobs：企业统一知识库
（上传→解析→分块→向量化入库）。MySQL 是事实源，ES/Milvus 是由 chunks 重建的投影。

Revision ID: 1c9e4a7b2d03
Revises: e7c4b2a190fd
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1c9e4a7b2d03"
down_revision: Union[str, Sequence[str], None] = "e7c4b2a190fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("uploader_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_ext", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("parser", sa.String(length=16), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.Double(), nullable=False),
        sa.Column("finished_at", sa.Double(), nullable=True),
    )
    op.create_index(
        "idx_knowledge_docs_tenant_updated",
        "knowledge_documents",
        ["tenant_id", "updated_at"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=36), nullable=False),
        sa.Column("doc_name", sa.String(length=255), nullable=False),
        sa.Column("chunk_title", sa.String(length=512), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("doc_pos", sa.Integer(), nullable=False),
        sa.Column("token_num", sa.Integer(), nullable=False),
        sa.Column("is_use", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Double(), nullable=False),
        sa.UniqueConstraint("doc_id", "doc_pos", name="uq_knowledge_chunks_doc_pos"),
    )
    op.create_index(
        "idx_knowledge_chunks_tenant_doc",
        "knowledge_chunks",
        ["tenant_id", "doc_id"],
    )

    op.create_table(
        "knowledge_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("heartbeat_at", sa.Double(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.Double(), nullable=True),
        sa.Column("finished_at", sa.Double(), nullable=True),
        sa.Column("created_at", sa.Double(), nullable=False),
    )
    op.create_index(
        "idx_knowledge_jobs_tenant_doc_created",
        "knowledge_jobs",
        ["tenant_id", "doc_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_jobs_tenant_doc_created", table_name="knowledge_jobs")
    op.drop_table("knowledge_jobs")
    op.drop_index("idx_knowledge_chunks_tenant_doc", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("idx_knowledge_docs_tenant_updated", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
