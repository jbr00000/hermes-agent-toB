from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from hermes_constants import get_hermes_home

from .models import Base

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
