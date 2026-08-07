"""Tenant-scoped sandbox identity and lifecycle helpers for Agent tasks."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path

from server.storage.repository import tenant_id

logger = logging.getLogger(__name__)

_SANDBOX_KEY_RE = re.compile(r"^tob-[0-9a-f]{32}$")


def task_sandbox_key(user_id: str, task_id: str) -> str:
    """Return an opaque, stable Docker/path key scoped to tenant, user and task."""
    identity = "\0".join((tenant_id(), user_id, task_id)).encode("utf-8")
    return f"tob-{hashlib.sha256(identity).hexdigest()[:32]}"


def _sandbox_directory(sandbox_key: str) -> Path:
    if not _SANDBOX_KEY_RE.fullmatch(sandbox_key):
        raise ValueError("invalid to-B sandbox key")

    from tools.environments.base import get_sandbox_dir

    parent = (get_sandbox_dir() / "docker").resolve()
    target = (parent / sandbox_key).resolve()
    if target.parent != parent:
        raise ValueError("sandbox path escapes the configured sandbox directory")
    return target


def _remove_task_container(sandbox_key: str) -> None:
    try:
        from tools.terminal_tool import cleanup_vm

        environment = cleanup_vm(sandbox_key, force_remove=True)
        wait_for_cleanup = getattr(environment, "wait_for_cleanup", None)
        if callable(wait_for_cleanup):
            wait_for_cleanup(timeout=30)
    except Exception:
        logger.warning("Could not clean active sandbox %s", sandbox_key, exc_info=True)

    try:
        from tools.environments.docker import remove_task_containers

        remove_task_containers(sandbox_key)
    except Exception:
        logger.warning("Could not remove persisted sandbox %s", sandbox_key, exc_info=True)


def release_task_sandbox(user_id: str, task_id: str) -> None:
    """Release task compute while retaining its persistent workspace for retry."""
    sandbox_key = task_sandbox_key(user_id, task_id)
    if not _sandbox_directory(sandbox_key).exists():
        return
    _remove_task_container(sandbox_key)


def destroy_task_sandbox(user_id: str, task_id: str) -> None:
    """Best-effort teardown of the task container and its persistent filesystem."""
    sandbox_key = task_sandbox_key(user_id, task_id)
    _remove_task_container(sandbox_key)
    target = _sandbox_directory(sandbox_key)
    shutil.rmtree(target, ignore_errors=True)
