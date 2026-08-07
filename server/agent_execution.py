"""Durable Agent job execution owned by a background worker."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from server.storage import get_repository, get_runtime_store

logger = logging.getLogger(__name__)


class AgentLeaseLost(RuntimeError):
    pass

_SENSITIVE_EVENT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


def _is_sensitive_event_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EVENT_KEYS:
        return True
    return any(
        normalized.startswith(f"{segment}_") or normalized.endswith(f"_{segment}")
        for segment in _SENSITIVE_EVENT_KEYS
    )


def _safe_event_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if _is_sensitive_event_key(key)
                else _safe_event_value(item, depth=depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_event_value(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return value[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


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
    tool_status_lock = threading.Lock()
    agent_holder: dict[str, object] = {}
    stop_heartbeat = threading.Event()
    timed_out = threading.Event()
    lease_lost = threading.Event()
    max_runtime = max(30, int(os.environ.get("HERMES_AGENT_RUN_TIMEOUT_SECONDS", "300")))

    if repository.start_task_run(request_id, worker_id) is None:
        return "ignored"
    runtime_store.mark_request(request_id, "running")
    runtime_store.save_chat_snapshot(
        request_id,
        session_id,
        "",
        event_sequence,
        started_at=snapshot_started_at,
    )

    def emit(event: str, data: dict[str, Any]) -> int:
        nonlocal event_sequence
        with event_lock:
            event_sequence += 1
            event_id = event_sequence
        if event != "delta":
            runtime_store.append_event(request_id, event_id, event, data)
        return event_id

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
            status=status,
            payload=_safe_event_value(payload),
        )
        emit(
            event_type,
            {**stored, "task_id": task_id, "request_id": request_id},
        )

    def on_tool_start(tool_call_id: str, tool_name: str, display_args: Any) -> None:
        if tool_name.startswith("_"):
            return
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
        emit_tool_event(
            "tool.completed",
            tool_name=tool_name,
            status="failed" if failed else "completed",
            payload={
                "tool_call_id": tool_call_id,
                "arguments": display_args,
                "result": result,
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
        if runtime_store.is_cancelled(request_id):
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
            if not runtime_store.heartbeat_agent_job(request_id, worker_id):
                lease_lost.set()
            now = time.monotonic()
            if now - last_database_heartbeat >= 10:
                repository.heartbeat_task_run(request_id, worker_id)
                last_database_heartbeat = now
            if now - run_started_at >= max_runtime:
                timed_out.set()
                runtime_store.cancel_request(request_id)
            if runtime_store.is_cancelled(request_id):
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
        if runtime_store.is_cancelled(request_id):
            raise RuntimeError("cancelled before execution started")
        from server.agent_factory import build_agent
        from server.audit import record_event
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
        if not prior_messages or title in {"New agent task", "新任务", "新智能体任务"}:
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
            or not runtime_store.heartbeat_agent_job(request_id, worker_id)
            or not repository.owns_task_run(request_id, worker_id)
        ):
            raise AgentLeaseLost(request_id)
        cancelled = runtime_store.is_cancelled(request_id) and not timed_out.is_set()
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
            finished_run = repository.finish_task_run(
                request_id,
                status="cancelled",
                task_status="cancelled",
                expected_worker_id=worker_id,
            )
            if finished_run is None:
                raise AgentLeaseLost(request_id)
            task_status = "cancelled"
        elif assistant_status == "failed":
            finished_run = repository.finish_task_run(
                request_id,
                status="failed",
                task_status="failed",
                error=error,
                expected_worker_id=worker_id,
            )
            if finished_run is None:
                raise AgentLeaseLost(request_id)
            task_status = "failed"
        elif phase == "plan":
            plan = repository.create_task_plan(
                user_id,
                task_id,
                final,
                request_id=request_id,
                expected_worker_id=worker_id,
            )
            if plan is None:
                raise AgentLeaseLost(request_id)
            emit(
                "plan.required",
                {"task_id": task_id, "request_id": request_id, "plan": plan},
            )
            task_status = "awaiting_approval"
        else:
            finished_run = repository.finish_task_run(
                request_id,
                status="completed",
                task_status="completed",
                expected_worker_id=worker_id,
            )
            if finished_run is None:
                raise AgentLeaseLost(request_id)
            task_status = "completed"
        repository.revoke_task_permissions(user_id, task_id)
        emit(
            "task.status",
            {
                "task_id": task_id,
                "request_id": request_id,
                "status": task_status,
                "permission_mode": "read",
            },
        )
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
        repository.finish_model_run(
            request_id,
            status=assistant_status,
            provider=runtime_metadata.get("provider"),
            model=runtime_metadata.get("model"),
            error=error,
        )
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
        if lease_lost.is_set() or not runtime_store.heartbeat_agent_job(
            request_id, worker_id
        ):
            logger.warning(
                "Agent request %s failed after losing its Worker lease", request_id
            )
            return "stale"
        cancelled = runtime_store.is_cancelled(request_id) and not timed_out.is_set()
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
            from server.audit import record_event

            if cancelled and assistant_message is None:
                assistant_message = repository.append_message(
                    session_id,
                    "assistant",
                    partial_content,
                    status="cancelled",
                    model_run_id=request_id,
                    duration_ms=max(0, int((time.monotonic() - run_started_at) * 1000)),
                )
            finished_run = repository.finish_task_run(
                request_id,
                status=status,
                task_status=status,
                error=error_text,
                expected_worker_id=worker_id,
            )
            if finished_run is None:
                logger.warning(
                    "Agent request %s could not persist failure after losing ownership",
                    request_id,
                )
                return "stale"
            repository.finish_model_run(request_id, status=status, error=error_text)
            repository.revoke_task_permissions(user_id, task_id)
            emit(
                "task.status",
                {
                    "task_id": task_id,
                    "request_id": request_id,
                    "status": status,
                    "permission_mode": "read",
                },
            )
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status=status,
                mode=phase,
                metadata={"request_id": request_id, "plan_state": plan_state},
                error=error_text,
            )
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
    repository.finish_model_run(request_id, status="failed", error=reason)
    repository.finish_task_run(
        request_id, status="failed", task_status="failed", error=reason
    )
    repository.revoke_task_permissions(user_id, task_id)
    event_id = runtime_store.last_event_id(request_id) + 1
    runtime_store.append_event(
        request_id,
        event_id,
        "task.status",
        {
            "task_id": task_id,
            "request_id": request_id,
            "status": "failed",
            "permission_mode": "read",
        },
    )
    event_id += 1
    runtime_store.append_event(
        request_id,
        event_id,
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
        event_id,
        status="failed",
        started_at=snapshot.get("started_at"),
        ttl_seconds=86400,
    )
