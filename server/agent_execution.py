"""Durable Agent job execution owned by a background worker."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from server.audit import record_event
from server.constants import is_default_agent_task_title
from server import tool_gate  # 导入即注册 pre_tool_call 审批门控钩子
from server.storage import get_repository, get_runtime_store
from server.tool_events import sanitize_tool_event_payload, tool_risk_level

logger = logging.getLogger(__name__)


class AgentLeaseLost(RuntimeError):
    pass

def _tool_result_failed(result: Any) -> bool:
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(parsed, dict):
        return False
    status = str(parsed.get("status") or "").strip().lower()
    exit_code = parsed.get("exit_code")
    return (
        status in {"error", "failed", "failure"}
        or bool(parsed.get("error"))
        or (isinstance(exit_code, int) and exit_code != 0)
    )


def _runtime_metadata(agent: object, mode: str) -> dict[str, Any]:
    return {
        "provider": getattr(agent, "provider", None),
        "model": getattr(agent, "model", None),
        "reasoning_config": getattr(agent, "reasoning_config", None),
        "enabled_toolsets": list(getattr(agent, "enabled_toolsets", None) or []),
        "mode": mode,
    }


def execute_agent_job(job: dict[str, Any], worker_id: str) -> str:
    repository = get_repository()
    runtime_store = get_runtime_store()
    request_id = str(job["request_id"])
    user_id = str(job["user_id"])
    task_id = str(job["task_id"])
    session_id = str(job["session_id"])
    phase = str(job["phase"])
    message = str(job["message"])
    permission_mode = str(job.get("permission_mode") or "read")
    if phase == "execute":
        permission_mode = repository.get_task_permission(user_id, task_id)["mode"]
    plan_state = str(job.get("plan_state") or phase)
    snapshot_started_at = float(job.get("queued_at") or time.time())
    run_started_at = time.monotonic()
    event_sequence = runtime_store.last_event_id(request_id)
    event_lock = threading.Lock()
    snapshot_lock = threading.Lock()
    snapshot_chunks: list[str] = []
    snapshot_last_saved_at = run_started_at
    tool_statuses: dict[str, str] = {}
    tool_started_at: dict[str, float] = {}
    tool_status_lock = threading.Lock()
    agent_holder: dict[str, object] = {}
    stop_heartbeat = threading.Event()
    timed_out = threading.Event()
    lease_lost = threading.Event()
    cancel_requested = threading.Event()
    # controlled 档等待人工审批可能长达数分钟，默认运行上限放宽到 3600s。
    _default_timeout = "3600" if permission_mode == "controlled" else "300"
    max_runtime = max(30, int(os.environ.get("HERMES_AGENT_RUN_TIMEOUT_SECONDS", _default_timeout)))

    started_run = repository.start_task_run(request_id, worker_id)
    if started_run is None:
        return "ignored"
    if started_run.get("cancel_requested_at") is not None:
        finished_run = repository.finalize_task_run(
            request_id,
            user_id=user_id,
            task_id=task_id,
            status="cancelled",
            task_status="cancelled",
            expected_worker_id=worker_id,
        )
        if finished_run is None:
            return "stale"
        status_event_id = runtime_store.next_event_id(request_id)
        runtime_store.append_event(
            request_id,
            status_event_id,
            "task.status",
            {
                "task_id": task_id,
                "request_id": request_id,
                "status": "cancelled",
                # 权限一次切换持久化：运行结束不再自动回落只读
                "permission_mode": repository.get_task_permission(user_id, task_id)["mode"],
            },
        )
        final_event_id = runtime_store.next_event_id(request_id)
        runtime_store.append_event(
            request_id,
            final_event_id,
            "final",
            {"content": "", "request_id": request_id, "status": "cancelled"},
        )
        event_sequence = max(event_sequence, final_event_id)
        runtime_store.save_chat_snapshot(
            request_id,
            session_id,
            "",
            event_sequence,
            status="cancelled",
            started_at=snapshot_started_at,
            ttl_seconds=86400,
        )
        runtime_store.mark_request(request_id, "done")
        return "cancelled"
    runtime_store.mark_request(request_id, "running")
    runtime_store.save_chat_snapshot(
        request_id,
        session_id,
        "",
        event_sequence,
        started_at=snapshot_started_at,
    )

    def durable_cancel_requested() -> bool:
        try:
            return repository.is_task_run_cancel_requested(request_id)
        except Exception:
            logger.warning(
                "Agent cancellation state read failed for %s",
                request_id,
                exc_info=True,
            )
            return False

    def emit(event: str, data: dict[str, Any]) -> int:
        nonlocal event_sequence
        if event != "delta":
            # 事件序号跨进程原子分配：API 入队路径（agent_queue）与本 worker
            # 会同时写同一运行的流，手工 last_event_id+1 撞号后，撞号事件会
            # 被 SSE 重放游标（after_id 严格大于）永久跳过——session 事件
            # 丢失曾导致前端用户消息双气泡。
            event_id = runtime_store.next_event_id(request_id)
            runtime_store.append_event(request_id, event_id, event, data)
            with event_lock:
                event_sequence = max(event_sequence, event_id)
            return event_id
        with event_lock:
            event_sequence += 1
            return event_sequence

    def persist_terminal(
        *,
        status: str,
        task_status: str,
        error: str | None,
        audit_metadata: dict[str, Any],
    ) -> bool:
        finished_run = repository.finalize_task_run(
            request_id,
            user_id=user_id,
            task_id=task_id,
            status=status,
            task_status=task_status,
            error=error,
            expected_worker_id=worker_id,
            provider=runtime_metadata.get("provider"),
            model=runtime_metadata.get("model"),
        )
        if finished_run is None:
            return False
        emit(
            "task.status",
            {
                "task_id": task_id,
                "request_id": request_id,
                "status": task_status,
                "permission_mode": repository.get_task_permission(user_id, task_id)["mode"],
            },
        )
        try:
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status=status,
                mode=phase,
                metadata=audit_metadata,
                error=error,
            )
        except Exception:
            logger.exception("Could not append terminal audit event for %s", request_id)
        return True

    def emit_tool_event(
        event_type: str,
        *,
        tool_name: str | None,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        stored = repository.record_tool_event(
            task_id=task_id,
            run_id=request_id,
            event_type=event_type,
            tool_name=tool_name,
            risk_level=tool_risk_level(tool_name),
            status=status,
            payload=sanitize_tool_event_payload(event_type, tool_name, payload),
        )
        emit(
            event_type,
            {**stored, "task_id": task_id, "request_id": request_id},
        )

    def on_tool_start(tool_call_id: str, tool_name: str, display_args: Any) -> None:
        if tool_name.startswith("_"):
            return
        tool_started_at[tool_call_id] = time.monotonic()
        emit_tool_event(
            "tool.started",
            tool_name=tool_name,
            status="running",
            payload={"tool_call_id": tool_call_id, "arguments": display_args},
        )

    def on_tool_complete(
        tool_call_id: str,
        tool_name: str,
        display_args: Any,
        result: Any,
    ) -> None:
        if tool_name.startswith("_"):
            return
        failed = _tool_result_failed(result)
        with tool_status_lock:
            tool_statuses[tool_name] = "failed" if failed else "completed"
        started_at = tool_started_at.pop(tool_call_id, None)
        duration_ms = (
            max(0, int((time.monotonic() - started_at) * 1000))
            if started_at is not None
            else 0
        )
        emit_tool_event(
            "tool.completed",
            tool_name=tool_name,
            status="failed" if failed else "completed",
            payload={
                "tool_call_id": tool_call_id,
                "arguments": display_args,
                "result": result,
                "duration_ms": duration_ms,
            },
        )

    def on_tool_progress(*args: Any, **kwargs: Any) -> None:
        event_name = str(args[0]) if args else "tool.progress"
        if event_name != "tool.progress":
            return
        tool_name = str(args[1]) if len(args) > 1 else None
        if tool_name and tool_name.startswith("_"):
            return
        emit_tool_event(
            "tool.progress",
            tool_name=tool_name,
            status="running",
            payload={"arguments": list(args[2:]), "metadata": kwargs},
        )

    def on_delta(chunk: str) -> None:
        nonlocal event_sequence, snapshot_last_saved_at
        if cancel_requested.is_set() or runtime_store.is_cancelled(request_id):
            cancel_requested.set()
            interrupt = getattr(agent_holder.get("agent"), "interrupt", None)
            if callable(interrupt):
                interrupt("cancelled by user")
            return
        if not chunk:
            return
        with event_lock:
            event_sequence += 1
            next_sequence = event_sequence
        snapshot_content: str | None = None
        with snapshot_lock:
            snapshot_chunks.append(chunk)
            now = time.monotonic()
            if now - snapshot_last_saved_at >= 0.2:
                snapshot_content = "".join(snapshot_chunks)
                snapshot_last_saved_at = now
        if snapshot_content is not None:
            runtime_store.save_chat_snapshot(
                request_id,
                session_id,
                snapshot_content,
                next_sequence,
                started_at=snapshot_started_at,
            )

    def heartbeat() -> None:
        last_database_heartbeat = 0.0
        while not stop_heartbeat.wait(2):
            runtime_store.touch_worker(worker_id)
            lease_state = runtime_store.heartbeat_agent_job(request_id, worker_id)
            if lease_state is False:
                lease_lost.set()
            now = time.monotonic()
            if now - last_database_heartbeat >= 10:
                try:
                    repository.heartbeat_task_run(request_id, worker_id)
                except Exception:
                    logger.warning(
                        "Agent database heartbeat failed for %s",
                        request_id,
                        exc_info=True,
                    )
                last_database_heartbeat = now
            if now - run_started_at >= max_runtime:
                timed_out.set()
                runtime_store.cancel_request(request_id)
            durable_cancelled = durable_cancel_requested()
            if runtime_store.is_cancelled(request_id) or durable_cancelled:
                cancel_requested.set()
            if cancel_requested.is_set():
                interrupt = getattr(agent_holder.get("agent"), "interrupt", None)
                if callable(interrupt):
                    interrupt("Agent task timed out" if timed_out.is_set() else "cancelled by user")
            elif lease_lost.is_set():
                interrupt = getattr(agent_holder.get("agent"), "interrupt", None)
                if callable(interrupt):
                    interrupt("Agent task lease was lost")

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        daemon=True,
        name=f"agent-heartbeat-{request_id[:8]}",
    )
    heartbeat_thread.start()

    runtime_metadata: dict[str, Any] = {"mode": phase}
    title = repository.get_owned_conversation(user_id, session_id)["title"]
    try:
        if cancel_requested.is_set() or durable_cancel_requested():
            cancel_requested.set()
            raise RuntimeError("cancelled before execution started")
        from server.agent_factory import build_agent
        from server.memory import save_memory_candidate
        from server.sandbox import task_sandbox_key

        prior_messages = [
            item
            for item in repository.get_messages(session_id)
            if item.get("model_run_id") != request_id
        ]
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in prior_messages
        ]
        if not prior_messages or is_default_agent_task_title(title):
            title = " ".join(message.split()).strip()[:40] or title
            repository.update_conversation(user_id, session_id, title=title)

        displayed_user_message = "执行已批准计划" if phase == "execute" else message
        user_message = repository.get_message_for_run(request_id, "user")
        if user_message is None:
            user_message = repository.append_message(
                session_id,
                "user",
                displayed_user_message,
                model_run_id=request_id,
            )
        emit(
            "session",
            {
                "session_id": session_id,
                "request_id": request_id,
                "title": title,
                "message": user_message,
            },
        )
        emit(
            "task.status",
            {
                "task_id": task_id,
                "request_id": request_id,
                "status": "planning" if phase == "plan" else "running",
                "permission_mode": permission_mode,
            },
        )

        tool_gate.register_run_gate(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            user_id=user_id,
            permission_mode=permission_mode,
            emit=emit,
        )
        agent = build_agent(
            session_id=session_id,
            user_id=user_id,
            prefill_messages=history,
            mode=phase,
            permission_mode=permission_mode,
            tool_progress_callback=on_tool_progress,
            tool_start_callback=on_tool_start,
            tool_complete_callback=on_tool_complete,
        )
        agent_holder["agent"] = agent
        runtime_metadata = _runtime_metadata(agent, phase)
        runtime_metadata["plan_state"] = plan_state
        runtime_metadata["permission_mode"] = permission_mode
        repository.update_conversation_model(
            session_id,
            model=runtime_metadata.get("model"),
            model_config={
                "provider": runtime_metadata.get("provider"),
                "reasoning_config": runtime_metadata.get("reasoning_config"),
                "enabled_toolsets": runtime_metadata.get("enabled_toolsets"),
                "mode": phase,
                "plan_state": plan_state,
            },
        )
        record_event(
            event_type="chat_turn",
            session_id=session_id,
            user_id=user_id,
            status="started",
            mode=phase,
            metadata={**runtime_metadata, "request_id": request_id},
        )

        final = agent.chat(
            message,
            stream_callback=on_delta,
            task_id=task_sandbox_key(user_id, task_id),
        ) or ""
        if (
            lease_lost.is_set()
            or runtime_store.heartbeat_agent_job(request_id, worker_id) is False
            or not repository.owns_task_run(request_id, worker_id)
        ):
            raise AgentLeaseLost(request_id)
        cancelled = cancel_requested.is_set() and not timed_out.is_set()
        with tool_status_lock:
            unresolved_tool_failure = any(
                status == "failed" for status in tool_statuses.values()
            )
        assistant_status = (
            "failed"
            if timed_out.is_set() or unresolved_tool_failure
            else "cancelled"
            if cancelled
            else "completed"
        )
        duration_ms = max(0, int((time.monotonic() - run_started_at) * 1000))
        assistant_message = repository.get_message_for_run(request_id, "assistant")
        if assistant_message is None:
            assistant_message = repository.append_message(
                session_id,
                "assistant",
                final,
                status=assistant_status,
                model_run_id=request_id,
                duration_ms=duration_ms,
            )
        if final and assistant_status == "completed":
            try:
                save_memory_candidate(user_id, session_id, message, final)
            except Exception:
                logger.debug("Could not save memory candidate", exc_info=True)

        error = (
            "Agent task timed out"
            if timed_out.is_set()
            else "one or more tools failed"
            if unresolved_tool_failure
            else None
        )
        if assistant_status == "cancelled":
            task_status = "cancelled"
            if not persist_terminal(
                status="cancelled",
                task_status=task_status,
                error=None,
                audit_metadata={
                    **runtime_metadata,
                    "request_id": request_id,
                    "response_chars": len(final),
                },
            ):
                raise AgentLeaseLost(request_id)
        elif assistant_status == "failed":
            task_status = "failed"
            if not persist_terminal(
                status="failed",
                task_status=task_status,
                error=error,
                audit_metadata={
                    **runtime_metadata,
                    "request_id": request_id,
                    "response_chars": len(final),
                },
            ):
                raise AgentLeaseLost(request_id)
        elif phase == "plan":
            plan = repository.create_task_plan(
                user_id,
                task_id,
                final,
                request_id=request_id,
                expected_worker_id=worker_id,
                provider=runtime_metadata.get("provider"),
                model=runtime_metadata.get("model"),
            )
            if plan is None:
                raise AgentLeaseLost(request_id)
            emit(
                "plan.required",
                {"task_id": task_id, "request_id": request_id, "plan": plan},
            )
            task_status = "awaiting_approval"
            emit(
                "task.status",
                {
                    "task_id": task_id,
                    "request_id": request_id,
                    "status": task_status,
                    "permission_mode": "read",
                },
            )
            try:
                record_event(
                    event_type="chat_turn",
                    session_id=session_id,
                    user_id=user_id,
                    status=assistant_status,
                    mode=phase,
                    metadata={
                        **runtime_metadata,
                        "request_id": request_id,
                        "response_chars": len(final),
                    },
                    error=error,
                )
            except Exception:
                logger.exception("Could not append terminal audit event for %s", request_id)
        else:
            task_status = "completed"
            if not persist_terminal(
                status="completed",
                task_status=task_status,
                error=None,
                audit_metadata={
                    **runtime_metadata,
                    "request_id": request_id,
                    "response_chars": len(final),
                },
            ):
                raise AgentLeaseLost(request_id)
        final_event_id = emit(
            "final",
            {
                "content": final,
                "message": assistant_message,
                "session_id": session_id,
                "request_id": request_id,
                "title": title,
                "status": assistant_status,
            },
        )
        runtime_store.save_chat_snapshot(
            request_id,
            session_id,
            final,
            final_event_id,
            status=assistant_status,
            started_at=snapshot_started_at,
            ttl_seconds=86400,
        )
        return assistant_status
    except AgentLeaseLost:
        logger.warning("Agent request %s stopped after losing its Worker lease", request_id)
        return "stale"
    except Exception as exc:
        if lease_lost.is_set() or runtime_store.heartbeat_agent_job(
            request_id, worker_id
        ) is False:
            logger.warning(
                "Agent request %s failed after losing its Worker lease", request_id
            )
            return "stale"
        cancelled = (
            cancel_requested.is_set()
            or runtime_store.is_cancelled(request_id)
            or durable_cancel_requested()
        ) and not timed_out.is_set()
        status = "cancelled" if cancelled else "failed"
        error_text = None if cancelled else f"{type(exc).__name__}: {exc}"
        if cancelled:
            logger.info("Agent request %s cancelled", request_id)
        else:
            logger.exception("Agent request %s failed", request_id)
        with snapshot_lock:
            partial_content = "".join(snapshot_chunks)
        assistant_message = repository.get_message_for_run(request_id, "assistant")
        try:
            if cancelled and assistant_message is None:
                assistant_message = repository.append_message(
                    session_id,
                    "assistant",
                    partial_content,
                    status="cancelled",
                    model_run_id=request_id,
                    duration_ms=max(0, int((time.monotonic() - run_started_at) * 1000)),
                )
            terminal_persisted = persist_terminal(
                status=status,
                task_status=status,
                error=error_text,
                audit_metadata={"request_id": request_id, "plan_state": plan_state},
            )
            if not terminal_persisted:
                logger.warning(
                    "Agent request %s could not persist failure after losing ownership",
                    request_id,
                )
                return "stale"
        except Exception:
            logger.exception("Failed to persist Agent terminal state")
        if cancelled:
            terminal_event_id = emit(
                "final",
                {
                    "content": partial_content,
                    "message": assistant_message,
                    "session_id": session_id,
                    "request_id": request_id,
                    "title": title,
                    "status": "cancelled",
                },
            )
        else:
            terminal_event_id = emit(
                "error",
                {
                    "message": "任务执行失败，请检查运行记录后重试",
                    "code": type(exc).__name__,
                    "request_id": request_id,
                },
            )
        runtime_store.save_chat_snapshot(
            request_id,
            session_id,
            partial_content,
            terminal_event_id,
            status=status,
            started_at=snapshot_started_at,
            ttl_seconds=86400,
        )
        return status
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=3)
        tool_gate.unregister_run_gate(session_id)
        try:
            repository.expire_pending_tool_approvals(request_id)
        except Exception:
            logger.warning(
                "Could not expire pending tool approvals for %s", request_id, exc_info=True
            )
        runtime_store.mark_request(request_id, "done")
        if phase == "execute":
            from server.sandbox import release_task_sandbox

            release_task_sandbox(user_id, task_id)


def fail_interrupted_execute_job(job: dict[str, Any], reason: str) -> None:
    repository = get_repository()
    runtime_store = get_runtime_store()
    request_id = str(job["request_id"])
    user_id = str(job["user_id"])
    task_id = str(job["task_id"])
    session_id = str(job["session_id"])
    repository.finalize_task_run(
        request_id,
        user_id=user_id,
        task_id=task_id,
        status="failed",
        task_status="failed",
        error=reason,
    )
    status_event_id = runtime_store.next_event_id(request_id)
    runtime_store.append_event(
        request_id,
        status_event_id,
        "task.status",
        {
            "task_id": task_id,
            "request_id": request_id,
            "status": "failed",
            # 权限一次切换持久化：运行结束不再自动回落只读
            "permission_mode": repository.get_task_permission(user_id, task_id)["mode"],
        },
    )
    error_event_id = runtime_store.next_event_id(request_id)
    runtime_store.append_event(
        request_id,
        error_event_id,
        "error",
        {
            "message": "执行 Worker 中断。为避免重复写入，任务未自动重放，请确认后重试。",
            "code": "worker_lost",
            "request_id": request_id,
        },
    )
    snapshot = runtime_store.get_chat_snapshot(request_id) or {}
    runtime_store.save_chat_snapshot(
        request_id,
        session_id,
        str(snapshot.get("content") or ""),
        error_event_id,
        status="failed",
        started_at=snapshot.get("started_at"),
        ttl_seconds=86400,
    )
