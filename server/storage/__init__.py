from __future__ import annotations

import threading

from .database import database_health, database_url, init_database, reset_database_for_tests
from .repository import StorageRepository
from .runtime import get_runtime_store, reset_runtime_store_for_tests

_repository: StorageRepository | None = None
_repository_lock = threading.Lock()


def get_repository() -> StorageRepository:
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = StorageRepository()
    return _repository


def init_storage() -> None:
    # SQLite is the zero-config test/development adapter. MySQL deployments
    # must advance through Alembic so schema changes remain auditable.
    if database_url().startswith("sqlite"):
        init_database()


def reset_storage_for_tests() -> None:
    global _repository
    with _repository_lock:
        _repository = None
    reset_runtime_store_for_tests()
    reset_database_for_tests()


__all__ = [
    "database_health",
    "get_repository",
    "get_runtime_store",
    "init_storage",
    "reset_storage_for_tests",
]
