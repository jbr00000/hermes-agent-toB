from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Double,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # Per-user feature flags (agent/chat/knowledge/memory); NULL = all enabled.
    features: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # True after account creation / admin password reset — the user must pick a
    # new password before the workbench unlocks.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("idx_auth_sessions_user_expires", "tenant_id", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    expires_at: Mapped[float] = mapped_column(Double, nullable=False)
    revoked_at: Mapped[float | None] = mapped_column(Double)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "idx_conversations_owner_updated",
            "tenant_id",
            "user_id",
            "interaction_type",
            "archived",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="headless")
    interaction_type: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    title: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model: Mapped[str | None] = mapped_column(String(128))
    model_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    plan_state: Mapped[str | None] = mapped_column(String(24))
    approved_at: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    ended_at: Mapped[float | None] = mapped_column(Double)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_no", name="uq_messages_conversation_sequence"),
        Index("idx_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    model_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # 结构化附件（如知识库问答的引用列表 citations），JSON 文本；
    # 命名避开 SQLAlchemy 保留字 metadata
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class ModelRun(Base):
    __tablename__ = "model_runs"
    __table_args__ = (
        Index("idx_model_runs_owner_started", "tenant_id", "user_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[float] = mapped_column(Double, nullable=False)
    completed_at: Mapped[float | None] = mapped_column(Double)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_agent_tasks_conversation"),
        Index("idx_agent_tasks_owner_updated", "tenant_id", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    current_run_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    completed_at: Mapped[float | None] = mapped_column(Double)


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index("idx_task_runs_task_started", "tenant_id", "task_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    heartbeat_at: Mapped[float | None] = mapped_column(Double)
    cancel_requested_at: Mapped[float | None] = mapped_column(Double)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[float] = mapped_column(Double, nullable=False)
    completed_at: Mapped[float | None] = mapped_column(Double)


class TaskPlan(Base):
    __tablename__ = "task_plans"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_task_plans_task_version"),
        Index("idx_task_plans_task_created", "tenant_id", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    approved_at: Mapped[float | None] = mapped_column(Double)


class PermissionLease(Base):
    __tablename__ = "permission_leases"
    __table_args__ = (
        Index("idx_permission_leases_task_active", "tenant_id", "task_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    # NULL = 持久权限：用户一次切换后长期生效，直到手动切回只读；
    # 非 NULL = 定时租约（兼容仍传 ttl_seconds 的旧客户端）
    expires_at: Mapped[float | None] = mapped_column(Double)
    revoked_at: Mapped[float | None] = mapped_column(Double)


class ToolApproval(Base):
    """controlled 权限档的运行中途审批：一条 terminal/process 命令一行。

    Status machine: pending → approved | denied | expired（超时/取消/运行结束）。
    command_preview 仅任务属主可见（可能含路径等敏感信息）；审计只落指纹。
    """

    __tablename__ = "tool_approvals"
    __table_args__ = (
        Index("idx_tool_approvals_task_status", "tenant_id", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    command_preview: Mapped[str] = mapped_column(String(512), nullable=False)
    args_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    decided_at: Mapped[float | None] = mapped_column(Double)


class ToolEvent(Base):
    __tablename__ = "tool_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_no", name="uq_tool_events_run_sequence"),
        Index("idx_tool_events_task_created", "tenant_id", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("idx_artifacts_task_created", "tenant_id", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    expires_at: Mapped[float | None] = mapped_column(Double)


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (Index("idx_memory_items_owner_created", "tenant_id", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class MemoryCandidate(Base):
    __tablename__ = "memory_candidates"
    __table_args__ = (
        Index(
            "idx_memory_candidates_owner_status",
            "tenant_id",
            "user_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    decided_at: Mapped[float | None] = mapped_column(Double)
    memory_id: Mapped[str | None] = mapped_column(String(36))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("idx_audit_events_session", "tenant_id", "conversation_id", "created_at"),
        Index("idx_audit_events_user", "tenant_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str | None] = mapped_column(String(24))
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


# Name of the base that legacy (pre-multi-base) documents are migrated into;
# also lazily created when an upload targets a tenant with no bases at all.
DEFAULT_KB_NAME = "默认知识库"


class KnowledgeBase(Base):
    """A named knowledge base — step ① of the build flow.

    Documents are uploaded into a base (step ②, status=uploaded) and only
    parsed when an admin explicitly selects them (step ③). doc_count /
    chunk_count are denormalized counters maintained by the repository.
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
        Index("idx_knowledge_bases_tenant_updated", "tenant_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Migration-created default bases have no creator → nullable.
    creator_id: Mapped[str | None] = mapped_column(String(36))
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)


class KnowledgeDocument(Base):
    """One uploaded file in a knowledge base.

    Status machine: uploaded → pending → parsing → syncing → ready / failed.
    ``uploaded`` means the file is stored but not yet queued for parsing
    (upload and parse are decoupled; admins pick documents to parse).
    MySQL is the source of truth; ES/Milvus are projections rebuilt from chunks.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("idx_knowledge_docs_tenant_updated", "tenant_id", "updated_at"),
        Index("idx_knowledge_docs_kb", "kb_id"),
        Index("idx_knowledge_docs_kb_hash", "kb_id", "file_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    uploader_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # 上传内容的 SHA-256，用于库内查重；存量行（迁移前上传）为 NULL，
    # 只对新上传生效
    file_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded")
    error: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(String(16))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Double)


class KnowledgeChunk(Base):
    """One searchable chunk; id doubles as the ES/Milvus primary key."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "doc_pos", name="uq_knowledge_chunks_doc_pos"),
        Index("idx_knowledge_chunks_tenant_doc", "tenant_id", "doc_id"),
        Index("idx_knowledge_chunks_kb", "kb_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Denormalized from the document for future per-base retrieval filtering.
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False)
    doc_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_title: Mapped[str | None] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_pos: Mapped[int] = mapped_column(Integer, nullable=False)
    token_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class KnowledgeJob(Base):
    """Durable build job for one document; consumed by the knowledge worker."""

    __tablename__ = "knowledge_jobs"
    __table_args__ = (
        Index("idx_knowledge_jobs_tenant_doc_created", "tenant_id", "doc_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    heartbeat_at: Mapped[float | None] = mapped_column(Double)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[float | None] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
