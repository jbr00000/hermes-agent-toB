"""API-side Agent job enqueueing and SSE event subscription."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any

from fastapi import HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from server.storage import get_repository, get_runtime_store

_embedded_workers: set[str] = set()
_embedded_workers_lock = threading.Lock()
_TERMINAL_EVENTS = {"final", "error"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _embedded_worker_allowed() -> bool:
    return os.environ.get("HERMES_ALLOW_EMBEDDED_AGENT_WORKER", "0") == "1"


def _start_embedded_worker(request_id: str) -> None:
    runtime_store = get_runtime_store()
    if runtime_store.redis_enabled or not _embedded_worker_allowed():
        return
    with _embedded_workers_lock:
        if request_id in _embedded_workers:
            return
        _embedded_workers.add(request_id)

    def run() -> None:
        try:
            from server.worker import AgentWorker

            AgentWorker(worker_id=f"embedded-{request_id[:12]}").run_once(
                timeout_seconds=0
            )
        finally:
            with _embedded_workers_lock:
                _embedded_workers.discard(request_id)

    threading.Thread(
        target=run,
        daemon=True,
        name=f"embedded-agent-{request_id[:8]}",
    ).start()


def enqueue_agent_run(
    *,
    user_id: str,
    task: dict[str, Any],
    request_id: str,
    phase: str,
    message: str,
    permission_mode: str,
    plan_state: str,
    display_message: str | None = None,
) -> None:
    repository = get_repository()
    runtime_store = get_runtime_store()
    if not runtime_store.redis_enabled and not _embedded_worker_allowed():
        raise HTTPException(
            status_code=503,
            detail="Agent queue requires Redis and a dedicated Worker",
        )
    now = time.time()
    job = {
        "request_id": request_id,
        "user_id": user_id,
        "task_id": task["id"],
        "session_id": task["session_id"],
        "phase": phase,
        "message": message,
        # 聊天流里展示的 user 气泡文案；None 时由执行侧按 phase 给默认值
        "display_message": display_message,
        "permission_mode": permission_mode,
        "plan_state": plan_state,
        "queued_at": now,
    }
    try:
        repository.enqueue_task_run(
            request_id,
            user_id,
            task["id"],
            phase,
            job,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        created = runtime_store.enqueue_agent_job(job)
    except RuntimeError as exc:
        repository.finish_task_run(
            request_id,
            status="failed",
            task_status="failed",
            error="Agent queue unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Agent queue is temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    runtime_store.mark_request(request_id, "queued")
    if runtime_store.get_chat_snapshot(request_id) is None:
        runtime_store.save_chat_snapshot(
            request_id,
            task["session_id"],
            "",
            0,
            status="queued",
            started_at=now,
            ttl_seconds=86400,
        )
    if created:
        # 序号必须原子分配：worker 可能已领走任务并开始写事件，手工
        # last_event_id+1 会与 worker 的 emit 撞号（撞号事件被重放游标跳过）
        event_id = runtime_store.next_event_id(request_id, ttl_seconds=86400)
        runtime_store.append_event(
            request_id,
            event_id,
            "task.status",
            {
                "task_id": task["id"],
                "request_id": request_id,
                "status": "queued",
                "phase": phase,
                "permission_mode": permission_mode,
            },
            ttl_seconds=86400,
        )
    _start_embedded_worker(request_id)


def stream_agent_run(
    request: Request,
    *,
    user_id: str,
    task_id: str,
    request_id: str,
    content_offset: int = 0,
) -> EventSourceResponse:
    repository = get_repository()
    run = repository.get_owned_model_run(user_id, request_id)
    task_run = repository.get_task_run(request_id)
    if (
        run is None
        or task_run is None
        or task_run["user_id"] != user_id
        or task_run["task_id"] != task_id
    ):
        raise HTTPException(status_code=404, detail="task run not found")
    runtime_store = get_runtime_store()

    async def event_gen():
        cursor = 0
        initial_snapshot = (
            await asyncio.to_thread(runtime_store.get_chat_snapshot, request_id) or {}
        )
        initial_content = str(initial_snapshot.get("content") or "")
        emitted_content = initial_content[: max(0, content_offset)]
        terminal_emitted = False
        terminal_snapshot_observed = False
        terminal_database_seen_at: float | None = None
        next_database_poll_at = 0.0
        while True:
            if await request.is_disconnected():
                break
            events = await asyncio.to_thread(
                runtime_store.list_events, request_id, after_id=cursor
            )
            terminal_events: list[dict[str, Any]] = []
            for event in events:
                event_id = int(event.get("id") or 0)
                cursor = max(cursor, event_id)
                if event.get("event") in _TERMINAL_EVENTS:
                    terminal_events.append(event)
                    continue
                yield {
                    "id": str(event_id),
                    "event": str(event.get("event") or "message"),
                    "data": json.dumps(event.get("data") or {}, ensure_ascii=False),
                }

            snapshot = await asyncio.to_thread(
                runtime_store.get_chat_snapshot, request_id
            )
            content = str(snapshot.get("content") or "") if snapshot else ""
            if content.startswith(emitted_content) and len(content) > len(emitted_content):
                yield {
                    "id": f"snapshot-{int(snapshot.get('sequence') or 0)}",
                    "event": "delta",
                    "data": json.dumps(
                        {
                            "content": content[len(emitted_content) :],
                            "request_id": request_id,
                        },
                        ensure_ascii=False,
                    ),
                }
                emitted_content = content
            elif not emitted_content and content:
                emitted_content = content

            for event in terminal_events:
                terminal_emitted = True
                yield {
                    "id": str(event.get("id") or cursor),
                    "event": str(event.get("event")),
                    "data": json.dumps(event.get("data") or {}, ensure_ascii=False),
                }

            now = time.monotonic()
            snapshot_terminal = bool(
                snapshot and snapshot.get("status") in _TERMINAL_STATUSES
            )
            current_run = None
            database_checked = False
            if terminal_events or snapshot_terminal or now >= next_database_poll_at:
                current_run = await asyncio.to_thread(repository.get_task_run, request_id)
                database_checked = True
                next_database_poll_at = now + 1.0
            if database_checked and (
                current_run is None or current_run["status"] in _TERMINAL_STATUSES
            ):
                now = time.monotonic()
                if terminal_database_seen_at is None:
                    terminal_database_seen_at = now
                if (
                    not terminal_emitted
                    and not snapshot_terminal
                    and now - terminal_database_seen_at < 2
                ):
                    await asyncio.sleep(0.05)
                    continue
                if (
                    not terminal_emitted
                    and snapshot_terminal
                    and not terminal_snapshot_observed
                ):
                    terminal_snapshot_observed = True
                    await asyncio.sleep(0.05)
                    continue
                if not terminal_emitted and current_run is not None:
                    terminal_status = current_run["status"]
                    yield {
                        "id": f"terminal-{cursor + 1}",
                        "event": "error" if terminal_status == "failed" else "final",
                        "data": json.dumps(
                            {
                                "request_id": request_id,
                                "status": terminal_status,
                                "content": content,
                                "message": current_run.get("error")
                                if terminal_status == "failed"
                                else None,
                            },
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "id": f"done-{cursor + 2}",
                    "event": "done",
                    "data": json.dumps(
                        {
                            "task_id": task_id,
                            "user_id": user_id,
                            "request_id": request_id,
                        },
                        ensure_ascii=False,
                    ),
                }
                break
            if database_checked:
                terminal_database_seen_at = None
            await asyncio.sleep(0.2)

    return EventSourceResponse(event_gen())
