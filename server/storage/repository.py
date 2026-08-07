from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from sqlalchemy import delete, func, select

from server.constants import DEFAULT_AGENT_TASK_TITLE, DEFAULT_CHAT_TITLE

from .database import session_scope
from .models import (
    AgentTask,
    AuditEvent,
    Artifact,
    AuthSession,
    Conversation,
    MemoryCandidate,
    MemoryItem,
    Message,
    ModelRun,
    PermissionLease,
    TaskPlan,
    TaskRun,
    ToolEvent,
    User,
)


def tenant_id() -> str:
    return (
        os.environ.get("HERMES_TENANT_ID")
        or os.environ.get("HERMES_CUSTOMER_ID")
        or "default"
    ).strip()


def _user_dict(row: User) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "status": row.status,
        "created_at": row.created_at,
    }


def _conversation_dict(row: Conversation) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "source": row.source,
        "interaction_type": row.interaction_type,
        "title": row.title
        or (DEFAULT_CHAT_TITLE if row.interaction_type == "chat" else DEFAULT_AGENT_TASK_TITLE),
        "status": row.status,
        "pinned": bool(row.pinned),
        "archived": bool(row.archived),
        "model": row.model,
        "model_config": row.model_config or {},
        "plan_state": row.plan_state,
        "approved_at": row.approved_at,
        "created_at": row.created_at,
        "started_at": row.created_at,
        "updated_at": row.updated_at,
        "ended_at": row.ended_at,
    }


def _task_dict(row: AgentTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "session_id": row.conversation_id,
        "source_session_id": row.source_session_id,
        "title": row.title,
        "status": row.status,
        "risk_level": row.risk_level,
        "current_run_id": row.current_run_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


def _task_run_dict(row: TaskRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "phase": row.phase,
        "attempt": row.attempt,
        "status": row.status,
        "worker_id": row.worker_id,
        "heartbeat_at": row.heartbeat_at,
        "cancel_requested_at": row.cancel_requested_at,
        "error": row.error,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


def _task_plan_dict(row: TaskPlan) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "version": row.version,
        "content": row.content,
        "status": row.status,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "created_at": row.created_at,
        "approved_at": row.approved_at,
    }


def _permission_dict(row: PermissionLease | None) -> dict[str, Any]:
    if row is None:
        return {"id": None, "mode": "read", "created_at": None, "expires_at": None}
    return {
        "id": row.id,
        "mode": row.mode,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }


def _tool_event_dict(row: ToolEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "run_id": row.run_id,
        "sequence": row.sequence_no,
        "event_type": row.event_type,
        "tool_name": row.tool_name,
        "risk_level": row.risk_level,
        "status": row.status,
        "payload": row.payload or {},
        "created_at": row.created_at,
    }


class StorageRepository:
    def create_user(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        now = time.time()
        row = User(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id(),
            username=username,
            password_hash=password_hash,
            role=role,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with session_scope() as session:
            session.add(row)
            session.flush()
            return _user_dict(row)

    def count_users(self) -> int:
        with session_scope() as session:
            return int(
                session.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant_id()))
                or 0
            )

    def get_user_by_username(self, username: str, *, include_password: bool = False) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(User).where(User.tenant_id == tenant_id(), User.username == username)
            )
            if row is None:
                return None
            result = _user_dict(row)
            if include_password:
                result["password_hash"] = row.password_hash
            return result

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(User).where(User.tenant_id == tenant_id(), User.id == user_id)
            )
            return _user_dict(row) if row is not None else None

    def list_users(self) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(User).where(User.tenant_id == tenant_id()).order_by(User.created_at)
            ).all()
            return [_user_dict(row) for row in rows]

    def delete_user(self, user_id: str) -> bool:
        with session_scope() as session:
            result = session.execute(
                delete(User).where(User.tenant_id == tenant_id(), User.id == user_id)
            )
            return bool(result.rowcount)

    def set_user_role(self, user_id: str, role: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(User).where(User.tenant_id == tenant_id(), User.id == user_id)
            )
            if row is None:
                return False
            row.role = role
            row.updated_at = time.time()
            return True

    def create_auth_session(
        self,
        user_id: str,
        token_hash: str,
        expires_at: float,
        user_agent: str | None,
    ) -> str:
        row = AuthSession(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id(),
            user_id=user_id,
            token_hash=token_hash,
            user_agent=(user_agent or "")[:255] or None,
            created_at=time.time(),
            expires_at=expires_at,
            revoked_at=None,
        )
        with session_scope() as session:
            session.add(row)
        return row.id

    def get_auth_session(self, token_hash: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(AuthSession).where(
                    AuthSession.tenant_id == tenant_id(),
                    AuthSession.token_hash == token_hash,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > time.time(),
                )
            )
            if row is None:
                return None
            return {"id": row.id, "user_id": row.user_id, "expires_at": row.expires_at}

    def revoke_auth_session(self, token_hash: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(AuthSession).where(
                    AuthSession.tenant_id == tenant_id(), AuthSession.token_hash == token_hash
                )
            )
            if row is None:
                return False
            row.revoked_at = time.time()
            return True

    def create_conversation(
        self,
        user_id: str,
        *,
        interaction_type: str = "chat",
        conversation_id: str | None = None,
        title: str | None = None,
        source: str = "headless",
        model: str | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        row = Conversation(
            id=conversation_id or str(uuid.uuid4()),
            tenant_id=tenant_id(),
            user_id=user_id,
            source=source,
            interaction_type=interaction_type,
            title=title,
            status="idle",
            pinned=False,
            archived=False,
            model=model,
            model_config=model_config or {},
            plan_state=None,
            created_at=now,
            updated_at=now,
        )
        with session_scope() as session:
            session.add(row)
            session.flush()
            return _conversation_dict(row)

    def ensure_conversation(self, conversation_id: str, user_id: str, **values: Any) -> dict[str, Any]:
        with session_scope() as session:
            row = session.get(Conversation, conversation_id)
            if row is None:
                now = time.time()
                interaction_type = values.get("interaction_type") or "agent"
                row = Conversation(
                    id=conversation_id,
                    tenant_id=tenant_id(),
                    user_id=user_id,
                    source=values.get("source") or "headless",
                    interaction_type=interaction_type,
                    title=values.get("title"),
                    status=values.get("status") or "idle",
                    pinned=False,
                    archived=False,
                    model=values.get("model"),
                    model_config=values.get("model_config") or {},
                    plan_state=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            elif row.tenant_id == tenant_id() and row.user_id == user_id:
                if values.get("model") is not None:
                    row.model = values["model"]
                if values.get("model_config") is not None:
                    row.model_config = values["model_config"]
                row.updated_at = time.time()
            return _conversation_dict(row)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id(), Conversation.id == conversation_id
                )
            )
            return _conversation_dict(row) if row is not None else None

    def get_owned_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id(),
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            return _conversation_dict(row) if row is not None else None

    def list_conversations(
        self,
        user_id: str,
        *,
        interaction_type: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = select(Conversation).where(
            Conversation.tenant_id == tenant_id(), Conversation.user_id == user_id
        )
        if interaction_type:
            query = query.where(Conversation.interaction_type == interaction_type)
        if not include_archived:
            query = query.where(Conversation.archived.is_(False))
        query = query.order_by(Conversation.pinned.desc(), Conversation.updated_at.desc()).limit(limit)
        with session_scope() as session:
            return [_conversation_dict(row) for row in session.scalars(query).all()]

    def update_conversation(self, user_id: str, conversation_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {"title", "pinned", "archived", "status", "model", "model_config", "ended_at"}
        with session_scope() as session:
            row = session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id(),
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if row is None:
                return None
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "title" and value is not None:
                    value = " ".join(str(value).split()).strip()[:100] or None
                setattr(row, key, value)
                if key == "title" and value is not None:
                    task = session.scalar(
                        select(AgentTask).where(
                            AgentTask.tenant_id == tenant_id(),
                            AgentTask.user_id == user_id,
                            AgentTask.conversation_id == conversation_id,
                        )
                    )
                    if task is not None:
                        task.title = value
                        task.updated_at = time.time()
            row.updated_at = time.time()
            session.flush()
            return _conversation_dict(row)

    def update_conversation_model(
        self,
        conversation_id: str,
        *,
        model: str | None,
        model_config: dict[str, Any],
    ) -> None:
        with session_scope() as session:
            row = session.get(Conversation, conversation_id)
            if row is not None and row.tenant_id == tenant_id():
                row.model = model or row.model
                row.model_config = model_config
                row.updated_at = time.time()

    def set_conversation_title(self, conversation_id: str, title: str) -> None:
        with session_scope() as session:
            row = session.get(Conversation, conversation_id)
            if row is not None and row.tenant_id == tenant_id():
                row.title = " ".join(title.split()).strip()[:100] or row.title
                row.updated_at = time.time()
                task = session.scalar(
                    select(AgentTask).where(
                        AgentTask.tenant_id == tenant_id(),
                        AgentTask.conversation_id == conversation_id,
                    )
                )
                if task is not None:
                    task.title = row.title or task.title
                    task.updated_at = row.updated_at

    def create_agent_task(
        self,
        user_id: str,
        title: str | None = None,
        source_session_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        conversation_id = str(uuid.uuid4())
        with session_scope() as session:
            source: Conversation | None = None
            source_messages: list[Message] = []
            if source_session_id:
                source = session.scalar(
                    select(Conversation).where(
                        Conversation.tenant_id == tenant_id(),
                        Conversation.user_id == user_id,
                        Conversation.id == source_session_id,
                    )
                )
                if source is None:
                    raise KeyError(source_session_id)
                if source.interaction_type != "chat":
                    raise ValueError("source_session_id must reference a Chat session")
                if source.status in {"queued", "running"}:
                    raise RuntimeError("source Chat session is still running")
                source_messages = list(
                    session.scalars(
                        select(Message)
                        .where(Message.conversation_id == source_session_id)
                        .order_by(Message.sequence_no)
                    ).all()
                )

            normalized_title = (
                " ".join((title or "").split()).strip()[:100]
                or (source.title if source and source.title else "")
                or DEFAULT_AGENT_TASK_TITLE
            )
            task = AgentTask(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id(),
                user_id=user_id,
                conversation_id=conversation_id,
                source_session_id=source_session_id,
                title=normalized_title,
                status="draft",
                risk_level="unknown",
                current_run_id=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            conversation = Conversation(
                id=conversation_id,
                tenant_id=tenant_id(),
                user_id=user_id,
                source="headless",
                interaction_type="agent",
                title=normalized_title,
                status="idle",
                pinned=False,
                archived=False,
                model=None,
                model_config={},
                plan_state=None,
                created_at=now,
                updated_at=now,
            )
            session.add_all([conversation, task])
            for source_message in source_messages:
                session.add(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation_id,
                        sequence_no=source_message.sequence_no,
                        role=source_message.role,
                        content=source_message.content,
                        status=source_message.status,
                        model_run_id=None,
                        duration_ms=source_message.duration_ms,
                        created_at=now,
                    )
                )
            session.flush()
            return _task_dict(task)

    def get_owned_task(self, user_id: str, task_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(AgentTask).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
            return _task_dict(row) if row is not None else None

    def get_task_by_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(AgentTask).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.conversation_id == conversation_id,
                )
            )
            return _task_dict(row) if row is not None else None

    def list_tasks(self, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(AgentTask)
                .join(Conversation, Conversation.id == AgentTask.conversation_id)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    Conversation.archived.is_(False),
                )
                .order_by(AgentTask.updated_at.desc())
                .limit(limit)
            ).all()
            return [_task_dict(row) for row in rows]

    def update_task(self, user_id: str, task_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {"title", "status", "risk_level", "current_run_id", "completed_at"}
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(AgentTask).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
            if row is None:
                return None
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "title" and value is not None:
                    value = " ".join(str(value).split()).strip()[:100] or row.title
                setattr(row, key, value)
            row.updated_at = now
            if "title" in changes:
                conversation = session.get(Conversation, row.conversation_id)
                if conversation is not None:
                    conversation.title = row.title
                    conversation.updated_at = now
            session.flush()
            return _task_dict(row)

    def create_task_run(
        self,
        request_id: str,
        user_id: str,
        task_id: str,
        phase: str,
        *,
        status: str = "running",
        request_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                raise KeyError(task_id)
            if task.current_run_id:
                raise RuntimeError("task already has an active run")
            attempt = int(
                session.scalar(
                    select(func.max(TaskRun.attempt)).where(
                        TaskRun.tenant_id == tenant_id(),
                        TaskRun.task_id == task_id,
                        TaskRun.phase == phase,
                    )
                )
                or 0
            ) + 1
            row = TaskRun(
                id=request_id,
                tenant_id=tenant_id(),
                user_id=user_id,
                task_id=task_id,
                phase=phase,
                attempt=attempt,
                status=status,
                request_payload=request_payload,
                worker_id=None,
                heartbeat_at=None,
                cancel_requested_at=None,
                error=None,
                started_at=now,
                completed_at=None,
            )
            session.add(row)
            task.current_run_id = request_id
            task.status = (
                "queued" if status == "queued" else "planning" if phase == "plan" else "running"
            )
            task.updated_at = now
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is not None:
                conversation.status = "running"
                conversation.updated_at = now
            session.flush()
            return _task_run_dict(row)

    def enqueue_task_run(
        self,
        request_id: str,
        user_id: str,
        task_id: str,
        phase: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                raise KeyError(task_id)
            existing = session.get(TaskRun, request_id)
            if existing is not None:
                if (
                    existing.tenant_id == tenant_id()
                    and existing.user_id == user_id
                    and existing.task_id == task_id
                    and existing.status in {"queued", "running"}
                ):
                    return _task_run_dict(existing)
                raise RuntimeError("request_id already exists")
            if task.current_run_id:
                raise RuntimeError("task already has an active run")
            attempt = int(
                session.scalar(
                    select(func.max(TaskRun.attempt)).where(
                        TaskRun.tenant_id == tenant_id(),
                        TaskRun.task_id == task_id,
                        TaskRun.phase == phase,
                    )
                )
                or 0
            ) + 1
            run = TaskRun(
                id=request_id,
                tenant_id=tenant_id(),
                user_id=user_id,
                task_id=task_id,
                phase=phase,
                attempt=attempt,
                status="queued",
                request_payload=request_payload,
                worker_id=None,
                heartbeat_at=None,
                cancel_requested_at=None,
                error=None,
                started_at=now,
                completed_at=None,
            )
            model_run = ModelRun(
                id=request_id,
                tenant_id=tenant_id(),
                user_id=user_id,
                conversation_id=task.conversation_id,
                status="queued",
                started_at=now,
            )
            session.add_all([run, model_run])
            task.current_run_id = request_id
            task.status = "queued"
            task.updated_at = now
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is not None:
                conversation.status = "queued"
                conversation.updated_at = now
            session.flush()
            return _task_run_dict(run)

    def start_task_run(self, request_id: str, worker_id: str) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun)
                .where(TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id)
                .with_for_update()
            )
            if row is None or row.status not in {"queued", "running"}:
                return None
            row.status = "running"
            row.worker_id = worker_id
            row.heartbeat_at = now
            task = session.get(AgentTask, row.task_id)
            if task is not None:
                task.status = "planning" if row.phase == "plan" else "running"
                task.updated_at = now
                conversation = session.get(Conversation, task.conversation_id)
                if conversation is not None:
                    conversation.status = "running"
                    conversation.updated_at = now
            model_run = session.get(ModelRun, request_id)
            if model_run is not None:
                model_run.status = "running"
            session.flush()
            return _task_run_dict(row)

    def heartbeat_task_run(self, request_id: str, worker_id: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun).where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.id == request_id,
                    TaskRun.status == "running",
                    TaskRun.worker_id == worker_id,
                )
            )
            if row is None:
                return False
            row.heartbeat_at = time.time()
            return True

    def request_task_run_cancel(self, user_id: str, request_id: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.user_id == user_id,
                    TaskRun.id == request_id,
                )
                .with_for_update()
            )
            if row is None or row.status not in {"queued", "running"}:
                return False
            if row.cancel_requested_at is None:
                row.cancel_requested_at = time.time()
            return True

    def is_task_run_cancel_requested(self, request_id: str) -> bool:
        with session_scope() as session:
            value = session.scalar(
                select(TaskRun.cancel_requested_at).where(
                    TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id
                )
            )
            return value is not None

    def owns_task_run(self, request_id: str, worker_id: str) -> bool:
        with session_scope() as session:
            return session.scalar(
                select(TaskRun.id).where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.id == request_id,
                    TaskRun.status == "running",
                    TaskRun.worker_id == worker_id,
                )
            ) is not None

    def requeue_task_run(self, request_id: str) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun)
                .where(TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id)
                .with_for_update()
            )
            if row is None or row.status not in {"queued", "running"}:
                return None
            row.status = "queued"
            row.worker_id = None
            row.heartbeat_at = None
            row.error = None
            task = session.get(AgentTask, row.task_id)
            if task is not None:
                task.current_run_id = request_id
                task.status = "queued"
                task.updated_at = now
                conversation = session.get(Conversation, task.conversation_id)
                if conversation is not None:
                    conversation.status = "queued"
                    conversation.updated_at = now
            model_run = session.get(ModelRun, request_id)
            if model_run is not None:
                model_run.status = "queued"
                model_run.completed_at = None
                model_run.error = None
            session.flush()
            return _task_run_dict(row)

    def get_task_run(self, request_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun).where(
                    TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id
                )
            )
            if row is None:
                return None
            result = _task_run_dict(row)
            result["user_id"] = row.user_id
            result["request_payload"] = row.request_payload or {}
            return result

    def list_recoverable_task_runs(self) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(TaskRun).where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.status.in_(("queued", "running")),
                )
            ).all()
            return [
                {
                    **_task_run_dict(row),
                    "user_id": row.user_id,
                    "request_payload": row.request_payload or {},
                }
                for row in rows
            ]

    def finish_task_run(
        self,
        request_id: str,
        *,
        status: str,
        task_status: str,
        error: str | None = None,
        expected_worker_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun).where(
                    TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id
                )
            )
            if row is None:
                return None
            if expected_worker_id is not None and (
                row.status != "running" or row.worker_id != expected_worker_id
            ):
                return None
            row.status = status
            row.error = error
            row.completed_at = now
            model_run = session.get(ModelRun, request_id)
            if model_run is not None and model_run.tenant_id == tenant_id():
                model_run.status = status
                model_run.error = error
                model_run.completed_at = now
            task = session.get(AgentTask, row.task_id)
            if task is not None and task.tenant_id == tenant_id():
                if task.current_run_id == request_id:
                    task.current_run_id = None
                task.status = task_status
                task.updated_at = now
                task.completed_at = now if task_status == "completed" else None
                conversation = session.get(Conversation, task.conversation_id)
                if conversation is not None:
                    conversation.status = "idle" if status == "completed" else task_status
                    conversation.ended_at = now if task_status == "completed" else None
                    conversation.updated_at = now
            session.flush()
            return _task_run_dict(row)

    def finalize_task_run(
        self,
        request_id: str,
        *,
        user_id: str,
        task_id: str,
        status: str,
        task_status: str,
        error: str | None = None,
        expected_worker_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.id == request_id,
                    TaskRun.user_id == user_id,
                    TaskRun.task_id == task_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if expected_worker_id is not None and (
                row.status != "running" or row.worker_id != expected_worker_id
            ):
                return None
            row.status = status
            row.error = error
            row.completed_at = now

            model_run = session.get(ModelRun, request_id)
            if model_run is not None and model_run.tenant_id == tenant_id():
                model_run.status = status
                model_run.provider = provider or model_run.provider
                model_run.model = model or model_run.model
                model_run.error = error
                model_run.completed_at = now

            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                return None
            if task.current_run_id == request_id:
                task.current_run_id = None
            task.status = task_status
            task.updated_at = now
            task.completed_at = now if task_status == "completed" else None
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is not None:
                conversation.status = "idle" if status == "completed" else task_status
                conversation.ended_at = now if task_status == "completed" else None
                conversation.updated_at = now

            leases = session.scalars(
                select(PermissionLease).where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.user_id == user_id,
                    PermissionLease.task_id == task_id,
                    PermissionLease.revoked_at.is_(None),
                )
            ).all()
            for lease in leases:
                lease.revoked_at = now
            session.flush()
            return _task_run_dict(row)

    def list_task_runs(self, user_id: str, task_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(TaskRun)
                .where(
                    TaskRun.tenant_id == tenant_id(),
                    TaskRun.user_id == user_id,
                    TaskRun.task_id == task_id,
                )
                .order_by(TaskRun.started_at, TaskRun.attempt)
            ).all()
            return [_task_run_dict(row) for row in rows]

    def create_task_plan(
        self,
        user_id: str,
        task_id: str,
        content: str,
        *,
        request_id: str | None = None,
        expected_worker_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            run = None
            if request_id is not None:
                run = session.scalar(
                    select(TaskRun)
                    .where(TaskRun.tenant_id == tenant_id(), TaskRun.id == request_id)
                    .with_for_update()
                )
                if run is None or (
                    expected_worker_id is not None
                    and (run.status != "running" or run.worker_id != expected_worker_id)
                ):
                    return None
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                raise KeyError(task_id)
            existing = session.scalars(
                select(TaskPlan).where(
                    TaskPlan.tenant_id == tenant_id(),
                    TaskPlan.task_id == task_id,
                    TaskPlan.status.in_(("pending", "approved")),
                )
            ).all()
            for prior in existing:
                prior.status = "superseded"
            version = int(
                session.scalar(
                    select(func.max(TaskPlan.version)).where(
                        TaskPlan.tenant_id == tenant_id(), TaskPlan.task_id == task_id
                    )
                )
                or 0
            ) + 1
            row = TaskPlan(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id(),
                task_id=task_id,
                version=version,
                content=content,
                status="pending",
                created_by=user_id,
                approved_by=None,
                created_at=now,
                approved_at=None,
            )
            session.add(row)
            task.status = "awaiting_approval"
            if run is not None:
                run.status = "completed"
                run.error = None
                run.completed_at = now
                model_run = session.get(ModelRun, request_id)
                if model_run is not None and model_run.tenant_id == tenant_id():
                    model_run.status = "completed"
                    model_run.provider = provider or model_run.provider
                    model_run.model = model or model_run.model
                    model_run.error = None
                    model_run.completed_at = now
                if task.current_run_id == request_id:
                    task.current_run_id = None
            task.updated_at = now
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is not None:
                conversation.plan_state = "plan_pending"
                conversation.approved_at = None
                conversation.status = "idle"
                conversation.updated_at = now
            leases = session.scalars(
                select(PermissionLease).where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.user_id == user_id,
                    PermissionLease.task_id == task_id,
                    PermissionLease.revoked_at.is_(None),
                )
            ).all()
            for lease in leases:
                lease.revoked_at = now
            session.flush()
            return _task_plan_dict(row)

    def get_latest_task_plan(self, user_id: str, task_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            owned_task = session.scalar(
                select(AgentTask.id).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
            if owned_task is None:
                return None
            row = session.scalar(
                select(TaskPlan)
                .where(TaskPlan.tenant_id == tenant_id(), TaskPlan.task_id == task_id)
                .order_by(TaskPlan.version.desc())
                .limit(1)
            )
            return _task_plan_dict(row) if row is not None else None

    def approve_task_plan(self, user_id: str, task_id: str) -> dict[str, Any] | None:
        now = time.time()
        with session_scope() as session:
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                return None
            row = session.scalar(
                select(TaskPlan)
                .where(
                    TaskPlan.tenant_id == tenant_id(),
                    TaskPlan.task_id == task_id,
                    TaskPlan.status == "pending",
                )
                .order_by(TaskPlan.version.desc())
                .limit(1)
            )
            if row is None:
                return None
            row.status = "approved"
            row.approved_by = user_id
            row.approved_at = now
            task.status = "ready"
            task.updated_at = now
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is not None:
                conversation.plan_state = "plan_approved"
                conversation.approved_at = now
                conversation.updated_at = now
            session.flush()
            return _task_plan_dict(row)

    def get_task_permission(self, user_id: str, task_id: str) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(PermissionLease)
                .where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.user_id == user_id,
                    PermissionLease.task_id == task_id,
                    PermissionLease.revoked_at.is_(None),
                    PermissionLease.expires_at > now,
                )
                .order_by(PermissionLease.created_at.desc())
                .limit(1)
            )
            return _permission_dict(row)

    def set_task_permission(
        self, user_id: str, task_id: str, mode: str, ttl_seconds: int
    ) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                raise KeyError(task_id)
            if task.current_run_id:
                raise RuntimeError("permission cannot change during a run")
            active = session.scalars(
                select(PermissionLease).where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.user_id == user_id,
                    PermissionLease.task_id == task_id,
                    PermissionLease.revoked_at.is_(None),
                )
            ).all()
            for lease in active:
                lease.revoked_at = now
            if mode == "read":
                return _permission_dict(None)
            row = PermissionLease(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id(),
                task_id=task_id,
                user_id=user_id,
                mode=mode,
                created_at=now,
                expires_at=now + ttl_seconds,
                revoked_at=None,
            )
            session.add(row)
            session.flush()
            return _permission_dict(row)

    def revoke_task_permissions(self, user_id: str, task_id: str) -> None:
        now = time.time()
        with session_scope() as session:
            rows = session.scalars(
                select(PermissionLease).where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.user_id == user_id,
                    PermissionLease.task_id == task_id,
                    PermissionLease.revoked_at.is_(None),
                )
            ).all()
            for row in rows:
                row.revoked_at = now

    def record_tool_event(
        self,
        *,
        task_id: str,
        run_id: str,
        event_type: str,
        status: str,
        tool_name: str | None = None,
        risk_level: str = "unknown",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            run = session.scalar(
                select(TaskRun)
                .where(TaskRun.tenant_id == tenant_id(), TaskRun.id == run_id)
                .with_for_update()
            )
            if run is None or run.task_id != task_id:
                raise KeyError(run_id)
            sequence = int(
                session.scalar(
                    select(func.max(ToolEvent.sequence_no)).where(ToolEvent.run_id == run_id)
                )
                or 0
            ) + 1
            row = ToolEvent(
                tenant_id=tenant_id(),
                task_id=task_id,
                run_id=run_id,
                sequence_no=sequence,
                event_type=event_type,
                tool_name=tool_name,
                risk_level=risk_level,
                status=status,
                payload=payload or {},
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _tool_event_dict(row)

    def list_tool_events(self, user_id: str, task_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            owned_task = session.scalar(
                select(AgentTask.id).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
            if owned_task is None:
                return []
            rows = session.scalars(
                select(ToolEvent)
                .where(ToolEvent.tenant_id == tenant_id(), ToolEvent.task_id == task_id)
                .order_by(ToolEvent.created_at, ToolEvent.id)
            ).all()
            return [_tool_event_dict(row) for row in rows]

    def list_artifacts(self, user_id: str, task_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            owned_task = session.scalar(
                select(AgentTask.id).where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
            )
            if owned_task is None:
                return []
            rows = session.scalars(
                select(Artifact)
                .where(Artifact.tenant_id == tenant_id(), Artifact.task_id == task_id)
                .order_by(Artifact.created_at)
            ).all()
            return [
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "run_id": row.run_id,
                    "name": row.name,
                    "path": row.storage_path,
                    "media_type": row.media_type,
                    "size_bytes": row.size_bytes,
                    "status": row.status,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                }
                for row in rows
            ]

    def delete_owned_task(self, user_id: str, task_id: str) -> str:
        """Delete a task aggregate while retaining its independent audit trail."""
        with session_scope() as session:
            task = session.scalar(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tenant_id(),
                    AgentTask.user_id == user_id,
                    AgentTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None:
                return "missing"
            if task.current_run_id:
                return "running"
            active_run = session.scalar(
                select(ModelRun.id)
                .where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.user_id == user_id,
                    ModelRun.conversation_id == task.conversation_id,
                    ModelRun.status == "running",
                )
                .limit(1)
            )
            if active_run is not None:
                return "running"

            session.add(
                AuditEvent(
                    tenant_id=tenant_id(),
                    event_type="task_delete",
                    conversation_id=task.conversation_id,
                    user_id=user_id,
                    status="completed",
                    mode="agent",
                    event_metadata={"task_id": task_id, "retained_audit": True},
                    error=None,
                    created_at=time.time(),
                )
            )
            session.execute(
                delete(ToolEvent).where(
                    ToolEvent.tenant_id == tenant_id(), ToolEvent.task_id == task_id
                )
            )
            session.execute(
                delete(Artifact).where(
                    Artifact.tenant_id == tenant_id(), Artifact.task_id == task_id
                )
            )
            session.execute(
                delete(PermissionLease).where(
                    PermissionLease.tenant_id == tenant_id(),
                    PermissionLease.task_id == task_id,
                )
            )
            session.execute(
                delete(TaskPlan).where(
                    TaskPlan.tenant_id == tenant_id(), TaskPlan.task_id == task_id
                )
            )
            session.execute(
                delete(TaskRun).where(
                    TaskRun.tenant_id == tenant_id(), TaskRun.task_id == task_id
                )
            )
            session.execute(delete(Message).where(Message.conversation_id == task.conversation_id))
            session.execute(
                delete(ModelRun).where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.user_id == user_id,
                    ModelRun.conversation_id == task.conversation_id,
                )
            )
            conversation = session.get(Conversation, task.conversation_id)
            session.delete(task)
            if conversation is not None:
                session.delete(conversation)
            return "deleted"

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: Any,
        *,
        status: str = "completed",
        model_run_id: str | None = None,
        duration_ms: int | None = None,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        now = created_at or time.time()
        stored_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        with session_scope() as session:
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id(), Conversation.id == conversation_id
                ).with_for_update()
            )
            if conversation is None:
                raise KeyError(f"conversation not found: {conversation_id}")
            sequence = int(
                session.scalar(
                    select(func.max(Message.sequence_no)).where(
                        Message.conversation_id == conversation_id
                    )
                )
                or 0
            ) + 1
            row = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                sequence_no=sequence,
                role=role,
                content=stored_content,
                status=status,
                model_run_id=model_run_id,
                duration_ms=duration_ms,
                created_at=now,
            )
            session.add(row)
            conversation.updated_at = now
            session.flush()
            return {
                "id": row.id,
                "session_id": conversation_id,
                "role": role,
                "content": content,
                "status": status,
                "model_run_id": model_run_id,
                "duration_ms": duration_ms,
                "timestamp": now,
                "created_at": now,
                "sequence_no": sequence,
            }

    def get_message_for_run(self, request_id: str, role: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Conversation.tenant_id == tenant_id(),
                    Message.model_run_id == request_id,
                    Message.role == role,
                )
                .order_by(Message.sequence_no)
                .limit(1)
            )
            if row is None:
                return None
            return {
                "id": row.id,
                "session_id": row.conversation_id,
                "role": row.role,
                "content": row.content,
                "status": row.status,
                "model_run_id": row.model_run_id,
                "duration_ms": row.duration_ms,
                "timestamp": row.created_at,
                "created_at": row.created_at,
                "sequence_no": row.sequence_no,
            }

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence_no)
            ).all()
            return [
                {
                    "id": row.id,
                    "session_id": row.conversation_id,
                    "role": row.role,
                    "content": row.content,
                    "status": row.status,
                    "model_run_id": row.model_run_id,
                    "duration_ms": row.duration_ms,
                    "timestamp": row.created_at,
                    "created_at": row.created_at,
                    "sequence_no": row.sequence_no,
                }
                for row in rows
            ]

    def get_message_count(self, conversation_id: str) -> int:
        with session_scope() as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(Message).where(
                        Message.conversation_id == conversation_id
                    )
                )
                or 0
            )

    def get_plan_state(self, conversation_id: str) -> dict[str, Any] | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None or conversation.get("plan_state") is None:
            return None
        return {
            "session_id": conversation_id,
            "user_id": conversation["user_id"],
            "state": conversation["plan_state"],
            "approved_at": conversation["approved_at"],
            "updated_at": conversation["updated_at"],
        }

    def set_plan_state(
        self,
        user_id: str,
        conversation_id: str,
        state: str,
        approved_at: float | None,
    ) -> dict[str, Any]:
        now = time.time()
        with session_scope() as session:
            row = session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id(),
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if row is None:
                raise KeyError(conversation_id)
            row.plan_state = state
            row.approved_at = approved_at
            row.updated_at = now
        return {
            "session_id": conversation_id,
            "user_id": user_id,
            "state": state,
            "approved_at": approved_at,
            "updated_at": now,
        }

    def create_model_run(
        self, request_id: str, user_id: str, conversation_id: str
    ) -> None:
        with session_scope() as session:
            session.add(
                ModelRun(
                    id=request_id,
                    tenant_id=tenant_id(),
                    user_id=user_id,
                    conversation_id=conversation_id,
                    status="running",
                    started_at=time.time(),
                )
            )

    def finish_model_run(
        self,
        request_id: str,
        *,
        status: str,
        provider: str | None = None,
        model: str | None = None,
        error: str | None = None,
    ) -> None:
        with session_scope() as session:
            row = session.get(ModelRun, request_id)
            if row is not None:
                row.status = status
                row.provider = provider or row.provider
                row.model = model or row.model
                row.error = error
                row.completed_at = time.time()

    def get_owned_model_run(self, user_id: str, request_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(ModelRun).where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.id == request_id,
                    ModelRun.user_id == user_id,
                )
            )
            if row is None:
                return None
            return {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "status": row.status,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
            }

    def get_active_model_run(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(ModelRun)
                .where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.user_id == user_id,
                    ModelRun.conversation_id == conversation_id,
                    ModelRun.status.in_(("queued", "running")),
                )
                .order_by(ModelRun.started_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            task_run = session.get(TaskRun, row.id)
            return {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "status": row.status,
                "phase": task_run.phase if task_run is not None else None,
                "provider": row.provider,
                "model": row.model,
                "started_at": row.started_at,
                "elapsed_ms": max(0, int((time.time() - row.started_at) * 1000)),
            }

    def delete_owned_conversation(self, user_id: str, conversation_id: str) -> str:
        """Delete conversation-owned data while retaining independent audit events."""
        with session_scope() as session:
            conversation = session.scalar(
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id(),
                    Conversation.user_id == user_id,
                    Conversation.id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                return "missing"
            active_run = session.scalar(
                select(ModelRun.id)
                .where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.user_id == user_id,
                    ModelRun.conversation_id == conversation_id,
                    ModelRun.status == "running",
                )
                .limit(1)
            )
            if conversation.status == "running" or active_run is not None:
                return "running"

            session.add(
                AuditEvent(
                    tenant_id=tenant_id(),
                    event_type="session_delete",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    status="completed",
                    mode=conversation.interaction_type,
                    event_metadata={"retained_audit": True},
                    error=None,
                    created_at=time.time(),
                )
            )
            session.execute(
                delete(MemoryCandidate).where(
                    MemoryCandidate.tenant_id == tenant_id(),
                    MemoryCandidate.user_id == user_id,
                    MemoryCandidate.conversation_id == conversation_id,
                )
            )
            session.execute(delete(Message).where(Message.conversation_id == conversation_id))
            session.execute(
                delete(ModelRun).where(
                    ModelRun.tenant_id == tenant_id(),
                    ModelRun.user_id == user_id,
                    ModelRun.conversation_id == conversation_id,
                )
            )
            session.delete(conversation)
            return "deleted"

    def save_memory(self, user_id: str, content: str) -> dict[str, Any]:
        now = time.time()
        row = MemoryItem(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id(),
            user_id=user_id,
            content=content,
            created_at=now,
        )
        with session_scope() as session:
            session.add(row)
        return {"id": row.id, "user_id": user_id, "content": content, "created_at": now}

    def list_memories(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(MemoryItem)
                .where(MemoryItem.tenant_id == tenant_id(), MemoryItem.user_id == user_id)
                .order_by(MemoryItem.created_at.desc())
            ).all()
            return [
                {"id": row.id, "content": row.content, "created_at": row.created_at}
                for row in rows
            ]

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        with session_scope() as session:
            result = session.execute(
                delete(MemoryItem).where(
                    MemoryItem.tenant_id == tenant_id(),
                    MemoryItem.user_id == user_id,
                    MemoryItem.id == memory_id,
                )
            )
            return bool(result.rowcount)

    def save_memory_candidate(
        self,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> dict[str, Any]:
        now = time.time()
        row = MemoryCandidate(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id(),
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message or "",
            assistant_message=assistant_message or "",
            status="pending",
            created_at=now,
        )
        with session_scope() as session:
            session.add(row)
        return {
            "id": row.id,
            "user_id": user_id,
            "session_id": conversation_id,
            "status": "pending",
            "created_at": now,
        }

    def list_memory_candidates(
        self, user_id: str, status: str | None = "pending"
    ) -> list[dict[str, Any]]:
        query = select(MemoryCandidate).where(
            MemoryCandidate.tenant_id == tenant_id(), MemoryCandidate.user_id == user_id
        )
        if status:
            query = query.where(MemoryCandidate.status == status)
        query = query.order_by(MemoryCandidate.created_at.desc())
        with session_scope() as session:
            rows = session.scalars(query).all()
            return [
                {
                    "id": row.id,
                    "session_id": row.conversation_id,
                    "user_message": row.user_message,
                    "assistant_message": row.assistant_message,
                    "status": row.status,
                    "created_at": row.created_at,
                    "decided_at": row.decided_at,
                    "memory_id": row.memory_id,
                }
                for row in rows
            ]

    def delete_memory_candidate(self, user_id: str, candidate_id: str) -> bool:
        with session_scope() as session:
            result = session.execute(
                delete(MemoryCandidate).where(
                    MemoryCandidate.tenant_id == tenant_id(),
                    MemoryCandidate.user_id == user_id,
                    MemoryCandidate.id == candidate_id,
                )
            )
            return bool(result.rowcount)

    def approve_memory_candidate(
        self, user_id: str, candidate_id: str, content: str | None
    ) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.scalar(
                select(MemoryCandidate).where(
                    MemoryCandidate.tenant_id == tenant_id(),
                    MemoryCandidate.user_id == user_id,
                    MemoryCandidate.id == candidate_id,
                    MemoryCandidate.status == "pending",
                )
            )
            if row is None:
                return None
            memory_content = (content or row.assistant_message or row.user_message).strip()
            if not memory_content:
                return None
            now = time.time()
            memory = MemoryItem(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id(),
                user_id=user_id,
                content=memory_content,
                created_at=now,
            )
            session.add(memory)
            row.status = "approved"
            row.decided_at = now
            row.memory_id = memory.id
            return {
                "id": memory.id,
                "user_id": user_id,
                "content": memory_content,
                "created_at": now,
                "candidate_id": candidate_id,
            }

    def record_audit_event(
        self,
        *,
        event_type: str,
        conversation_id: str | None,
        user_id: str | None,
        status: str,
        mode: str | None,
        metadata: dict[str, Any],
        error: str | None,
    ) -> int:
        row = AuditEvent(
            tenant_id=tenant_id(),
            event_type=event_type,
            conversation_id=conversation_id,
            user_id=user_id,
            status=status,
            mode=mode,
            event_metadata=metadata,
            error=error,
            created_at=time.time(),
        )
        with session_scope() as session:
            session.add(row)
            session.flush()
            return int(row.id)

    def list_audit_events(
        self, *, conversation_id: str | None = None, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id())
        if conversation_id is not None:
            query = query.where(AuditEvent.conversation_id == conversation_id)
        if user_id is not None:
            query = query.where(AuditEvent.user_id == user_id)
        query = query.order_by(AuditEvent.created_at, AuditEvent.id)
        with session_scope() as session:
            rows = session.scalars(query).all()
            return [
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "session_id": row.conversation_id,
                    "user_id": row.user_id,
                    "status": row.status,
                    "mode": row.mode,
                    "metadata": row.event_metadata or {},
                    "error": row.error,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
