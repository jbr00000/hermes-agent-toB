"""controlled 权限档的运行中途审批门控。

实现机制：插件 pre_tool_call 钩子（agent 线程内同步执行、可阻塞）。agent 以
controlled 权限运行时，每条 terminal/process 命令与 write_file/patch 文件写入
都在这里挂起，直到用户在 Web 上批准/拒绝、运行被取消或审批超时。

关键约束（与 agent_execution / 前端约定）:
- 门控注册表按 ``session_id`` 索引（钩子里的 ``api_request_id`` 每次模型调用
  都变；``task_id`` 是 sandbox key 而非数据库任务 id——两者都不能用）。
- 钩子不直接写 SSE：注册 gate 时带上 executor 的 ``emit`` 闭包，由它统一发
  ``tool.approval_required`` / ``tool.approval_resolved``，避免事件序号撞号。
- 钩子框架层 fail-open（异常被吞），因此钩子体内 catch 一切，异常即拦截
  （fail-closed）。
- 审计只落 tool_name + 参数指纹；原始命令只进 tool_approvals.command_preview
  （仅任务属主可见，可能含路径等敏感信息）。
- ``allow_all``（本次运行全部允许）走 RuntimeStore flag，Redis 部署下跨进程
  生效，不落业务库。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

GATED_TOOLS = frozenset({"terminal", "process", "write_file", "patch"})
DENIED_SENTINEL = "[approval-denied]"

EmitFn = Callable[[str, dict[str, Any]], int]


@dataclass
class _RunGate:
    session_id: str
    request_id: str
    task_id: str
    user_id: str
    permission_mode: str
    emit: EmitFn


_gates: dict[str, _RunGate] = {}
_gates_lock = threading.Lock()


def register_run_gate(
    *,
    session_id: str,
    request_id: str,
    task_id: str,
    user_id: str,
    permission_mode: str,
    emit: EmitFn,
) -> None:
    with _gates_lock:
        _gates[session_id] = _RunGate(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            user_id=user_id,
            permission_mode=permission_mode,
            emit=emit,
        )


def unregister_run_gate(session_id: str) -> None:
    with _gates_lock:
        _gates.pop(session_id, None)


def _approval_timeout_seconds() -> int:
    raw = os.environ.get("HERMES_TOOL_APPROVAL_TIMEOUT_SECONDS", "300").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 300


def _command_preview(tool_name: str, args: dict[str, Any]) -> str:
    """人类可读的命令预览（截断 500 字符）；只进 tool_approvals 表，不进审计。"""
    command = args.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()[:500]
    action = args.get("action")
    if isinstance(action, str) and action.strip():
        return f"{tool_name}:{action.strip()}"[:500]
    try:
        return json.dumps(args, ensure_ascii=False)[:500]
    except (TypeError, ValueError):
        return tool_name


def _args_fingerprint(args: dict[str, Any]) -> str:
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted(args.keys()))
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def tool_approval_gate_hook(
    *,
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    session_id: str = "",
    **_: Any,
) -> dict[str, str] | None:
    """pre_tool_call 钩子：controlled 运行中拦截 terminal/process 等待批准。

    返回 None 放行；返回 {"action": "block", "message": ...} 拦截。message 带
    DENIED_SENTINEL 前缀，供模型与前端识别"这是审批拒绝，不是命令执行失败"。
    """
    if tool_name not in GATED_TOOLS:
        return None
    with _gates_lock:
        gate = _gates.get(session_id)
    if gate is None or gate.permission_mode != "controlled":
        return None
    try:
        from server.audit import record_event
        from server.storage import get_repository
        from server.storage.runtime import get_runtime_store

        runtime = get_runtime_store()
        if runtime.is_run_flag(gate.request_id, "allow_all"):
            return None

        repository = get_repository()
        call_args = args if isinstance(args, dict) else {}
        fingerprint = _args_fingerprint(call_args)
        approval = repository.create_tool_approval(
            task_id=gate.task_id,
            run_request_id=gate.request_id,
            user_id=gate.user_id,
            tool_name=tool_name,
            command_preview=_command_preview(tool_name, call_args),
            args_fingerprint=fingerprint,
        )
        approval_id = approval["id"]

        def _audit(status: str) -> None:
            try:
                record_event(
                    event_type="tool_approval",
                    session_id=gate.session_id,
                    user_id=gate.user_id,
                    status=status,
                    mode="controlled",
                    metadata={
                        "approval_id": approval_id,
                        "tool_name": tool_name,
                        "args_fingerprint": fingerprint,
                    },
                )
            except Exception:
                logger.warning("tool_approval audit failed for %s", approval_id, exc_info=True)

        def _resolved(final_status: str) -> None:
            gate.emit(
                "tool.approval_resolved",
                {
                    "approval_id": approval_id,
                    "task_id": gate.task_id,
                    "request_id": gate.request_id,
                    "tool_name": tool_name,
                    "status": final_status,
                },
            )

        _audit("pending")
        gate.emit(
            "tool.approval_required",
            {**approval, "request_id": gate.request_id},
        )

        timeout = _approval_timeout_seconds()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if runtime.is_cancelled(gate.request_id) or (
                repository.is_task_run_cancel_requested(gate.request_id)
            ):
                expired = repository.expire_tool_approval(approval_id)
                if expired is not None:
                    _audit("expired")
                    _resolved("expired")
                return {
                    "action": "block",
                    "message": f"{DENIED_SENTINEL} 运行已取消，命令未执行",
                }
            row = repository.get_tool_approval(approval_id)
            if row is not None and row["status"] != "pending":
                decision = row["status"]
                _audit(decision)
                _resolved(decision)
                if decision == "approved":
                    return None
                return {
                    "action": "block",
                    "message": f"{DENIED_SENTINEL} 用户拒绝了该命令，未执行",
                }
            time.sleep(1)

        expired = repository.expire_tool_approval(approval_id)
        if expired is not None:
            _audit("expired")
            _resolved("expired")
        return {
            "action": "block",
            "message": f"{DENIED_SENTINEL} 审批超时（{timeout} 秒），命令未执行",
        }
    except Exception:
        # 钩子框架 fail-open（异常被吞），门控必须 fail-closed。
        logger.exception("tool approval gate failed closed for session %s", session_id)
        return {
            "action": "block",
            "message": f"{DENIED_SENTINEL} 审批门控异常，命令已被拦截",
        }


def _register_hook_once() -> None:
    try:
        from hermes_cli.plugins import register_hook

        register_hook("pre_tool_call", tool_approval_gate_hook)
    except Exception:
        logger.warning("could not register tool approval gate hook", exc_info=True)


_register_hook_once()
