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
_TASK_ID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")


def user_sandbox_key(user_id: str) -> str:
    """Per-user sandbox key: one long-lived Docker sandbox per user.

    粒度是用户而非任务：同一用户的所有 Agent 任务共用一个容器和一个
    持久工作区（任务间通过 workspace/tasks/<task_id>/ 子目录隔离），
    不同用户之间互不可见。to-B 单租户 10–50 用户共享实例，绝不能全局
    共用一个沙箱——那会击穿 user_id 隔离边界。
    """
    identity = "\0".join((tenant_id(), user_id)).encode("utf-8")
    return f"tob-{hashlib.sha256(identity).hexdigest()[:32]}"


def task_sandbox_key(user_id: str, task_id: str) -> str:
    """Backward-compatible alias: the sandbox is now per-user, not per-task."""
    return user_sandbox_key(user_id)


def _sandbox_directory(sandbox_key: str) -> Path:
    if not _SANDBOX_KEY_RE.fullmatch(sandbox_key):
        raise ValueError("invalid to-B sandbox key")

    from tools.environments.base import get_sandbox_dir

    parent = (get_sandbox_dir() / "docker").resolve()
    target = (parent / sandbox_key).resolve()
    if target.parent != parent:
        raise ValueError("sandbox path escapes the configured sandbox directory")
    return target


def user_workspace_dir(user_id: str) -> Path:
    """Host path of the user's persistent sandbox workspace (container /workspace)."""
    return _sandbox_directory(user_sandbox_key(user_id)) / "workspace"


def task_workspace_dir(user_id: str, task_id: str, *, create: bool = True) -> Path:
    """Host path of one task's subdirectory inside the user's sandbox workspace.

    容器内对应 /workspace/tasks/<task_id>/；terminal/file 工具的 cwd override
    也指到这里。产物下载接口直接读这个宿主机目录（绑定挂载，容器回收后保留）。
    """
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid task id for workspace path")
    base = user_workspace_dir(user_id).resolve()
    target = (base / "tasks" / task_id).resolve()
    if target.parent.parent != base or target.parent.name != "tasks":
        raise ValueError("task workspace path escapes the sandbox workspace")
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def task_container_cwd(task_id: str) -> str:
    """Container-side cwd for a task inside the shared user sandbox."""
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid task id for container cwd")
    return f"/workspace/tasks/{task_id}"


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
    """No-op: the sandbox is per-user and long-lived, shared by all the user's tasks.

    运行结束不回收容器——它可能正在服务该用户的其他任务。保留函数签名以免
    旧调用方崩溃；新代码不应再调用。
    """
    return None


def destroy_task_sandbox(user_id: str, task_id: str) -> None:
    """删除任务时只清理该任务的工作区子目录，不动共享的用户沙箱容器。"""
    try:
        target = task_workspace_dir(user_id, task_id, create=False)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)


def destroy_user_sandbox(user_id: str) -> None:
    """删除用户时的完整清理：移除容器 + 整个工作区。供用户删除流程调用。"""
    sandbox_key = user_sandbox_key(user_id)
    _remove_task_container(sandbox_key)
    target = _sandbox_directory(sandbox_key)
    shutil.rmtree(target, ignore_errors=True)
