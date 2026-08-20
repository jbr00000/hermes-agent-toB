from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from hermes_constants import get_hermes_home

from .models import DEFAULT_KB_NAME, Base
from .user_features import DEFAULT_USER_FEATURES

_lock = threading.Lock()
_engine: Engine | None = None
_engine_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url() -> str:
    configured = os.environ.get("HERMES_DATABASE_URL", "").strip()
    if configured:
        return configured
    path = Path(get_hermes_home()) / "hermes_tob.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _build_engine(url: str) -> Engine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_recycle=int(os.environ.get("HERMES_DATABASE_POOL_RECYCLE", "1800")),
            pool_size=int(os.environ.get("HERMES_DATABASE_POOL_SIZE", "10")),
            max_overflow=int(os.environ.get("HERMES_DATABASE_MAX_OVERFLOW", "20")),
        )
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    global _engine, _engine_url, _session_factory
    url = database_url()
    if _engine is None or _engine_url != url:
        with _lock:
            if _engine is None or _engine_url != url:
                if _engine is not None:
                    _engine.dispose()
                _engine = _build_engine(url)
                _engine_url = url
                _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    Base.metadata.create_all(get_engine())
    _ensure_added_columns(get_engine())
    _ensure_knowledge_kb_ids(get_engine())
    _ensure_superadmin(get_engine())


# Columns added after the table first shipped. ``create_all`` never alters
# existing tables, so the SQLite zero-config path needs this table-driven
# shim; MySQL deployments instead advance through Alembic (auditable).
# Each entry: (table, column, DDL type, backfill JSON value or None).
_ADDED_COLUMNS: tuple[tuple[str, str, str, dict | None], ...] = (
    ("users", "features", "JSON", DEFAULT_USER_FEATURES),
    ("users", "must_change_password", "BOOLEAN NOT NULL DEFAULT 0", None),
    ("knowledge_documents", "kb_id", "VARCHAR(36)", None),
    ("knowledge_documents", "file_hash", "VARCHAR(64)", None),
    ("knowledge_chunks", "kb_id", "VARCHAR(36)", None),
    ("messages", "metadata_json", "TEXT", None),
)


def _ensure_added_columns(engine: Engine) -> None:
    """Add post-hoc columns on SQLite and backfill them. Idempotent."""
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table, column, ddl, backfill in _ADDED_COLUMNS:
            exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).scalar()
            if not exists:
                continue
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if column in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            if backfill is not None:
                conn.execute(
                    text(f"UPDATE {table} SET {column} = :v WHERE {column} IS NULL"),
                    {"v": json.dumps(backfill)},
                )


def _ensure_knowledge_kb_ids(engine: Engine) -> None:
    """Backfill kb_id on legacy knowledge rows (SQLite path). Idempotent.

    Mirrors the Alembic data migration for MySQL: every tenant with documents
    but no knowledge base gets a default base, and documents/chunks pointing
    at nothing are attached to it. Fresh databases have no NULL kb_id rows,
    so this is a no-op there.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as conn:
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_bases'")
        ).scalar():
            return
        tenant_rows = conn.execute(
            text("SELECT DISTINCT tenant_id FROM knowledge_documents "
                 "WHERE kb_id IS NULL OR kb_id = ''")
        ).fetchall()
        now = time.time()
        for (tenant_id,) in tenant_rows:
            kb_id = conn.execute(
                text("SELECT id FROM knowledge_bases WHERE tenant_id = :t AND name = :n"),
                {"t": tenant_id, "n": DEFAULT_KB_NAME},
            ).scalar()
            if kb_id is None:
                kb_id = uuid.uuid4().hex
                conn.execute(
                    text("INSERT INTO knowledge_bases "
                         "(id, tenant_id, name, description, creator_id, doc_count, "
                         " chunk_count, created_at, updated_at) "
                         "VALUES (:id, :t, :n, NULL, NULL, 0, 0, :now, :now)"),
                    {"id": kb_id, "t": tenant_id, "n": DEFAULT_KB_NAME, "now": now},
                )
            conn.execute(
                text("UPDATE knowledge_documents SET kb_id = :kb "
                     "WHERE tenant_id = :t AND (kb_id IS NULL OR kb_id = '')"),
                {"kb": kb_id, "t": tenant_id},
            )
        # Chunks follow their document (denormalized copy).
        conn.execute(
            text("UPDATE knowledge_chunks SET kb_id = ("
                 "  SELECT d.kb_id FROM knowledge_documents d"
                 "  WHERE d.id = knowledge_chunks.doc_id) "
                 "WHERE kb_id IS NULL OR kb_id = ''")
        )
        # 计数与现状对齐（之后由 repository 增量维护）。
        conn.execute(
            text("UPDATE knowledge_bases SET "
                 "doc_count = (SELECT COUNT(*) FROM knowledge_documents d "
                 "              WHERE d.kb_id = knowledge_bases.id), "
                 "chunk_count = (SELECT COUNT(*) FROM knowledge_chunks c "
                 "                WHERE c.kb_id = knowledge_bases.id)")
        )


def _ensure_superadmin(engine: Engine) -> None:
    """Promote the oldest active admin to superadmin when none exists (SQLite path).

    Mirrors the Alembic data migration in 3b7e1f9a42c6 for MySQL: deployments
    bootstrapped before the superadmin role existed only have admin/user rows,
    and user management is superadmin-only. Idempotent — a no-op once any
    active superadmin exists.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as conn:
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'")
        ).scalar():
            return
        has_superadmin = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'superadmin' AND status = 'active'")
        ).scalar()
        if has_superadmin:
            return
        oldest = conn.execute(
            text("SELECT id, username FROM users WHERE role = 'admin' AND status = 'active' "
                 "ORDER BY created_at ASC LIMIT 1")
        ).first()
        if oldest is None:
            return
        conn.execute(
            text("UPDATE users SET role = 'superadmin' WHERE id = :id"), {"id": oldest[0]}
        )
        print(f"[auth] WARNING: promoted oldest admin '{oldest[1]}' to superadmin (upgrade path).")


def database_health() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_database_for_tests() -> None:
    global _engine, _engine_url, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _session_factory = None
