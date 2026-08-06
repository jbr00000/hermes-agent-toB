"""Authenticated SSE Chat/Agent endpoint with durable conversation storage."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from server.deps import get_current_user
from server.storage import get_repository, get_runtime_store

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    interaction_type: Literal["chat", "agent"] | None = None
    mode: Optional[str] = None


_active_agents: dict[str, tuple[str, object]] = {}
_active_agents_lock = threading.Lock()


def _agent_runtime_metadata(agent, mode: str | None) -> dict:
    return {
        "provider": getattr(agent, "provider", None),
        "model": getattr(agent, "model", None),
        "reasoning_config": getattr(agent, "reasoning_config", None),
        "enabled_toolsets": list(getattr(agent, "enabled_toolsets", None) or []),
        "mode": mode or "chat",
    }


def _interrupt_local_agent(request_id: str, user_id: str) -> bool:
    with _active_agents_lock:
        active = _active_agents.get(request_id)
    if active is None or active[0] != user_id:
        return False
    interrupt = getattr(active[1], "interrupt", None)
    if callable(interrupt):
        interrupt("cancelled by user")
    return True


@router.post("/chat")
async def chat(req: ChatRequest, request: Request, user: dict = Depends(get_current_user)):
    repository = get_repository()
    runtime_store = get_runtime_store()
    user_id = user["id"]
    # Legacy callers that send plan/execute without interaction_type remain
    # Agent calls. New browser clients must declare Chat explicitly.
    interaction_type = req.interaction_type or ("agent" if req.mode else "chat")

    if req.session_id:
        from server.sessions import assert_session_owned

        assert_session_owned(user_id, req.session_id)
        session_id = req.session_id
        existing = repository.get_conversation(session_id)
        if existing is None:
            repository.ensure_conversation(
                session_id, user_id, source="headless", interaction_type=interaction_type
            )
        elif existing.get("interaction_type") != interaction_type:
            raise HTTPException(status_code=409, detail="session interaction type mismatch")
    else:
        session_id = repository.create_conversation(
            user_id, interaction_type=interaction_type, source="headless"
        )["id"]

    if interaction_type == "chat":
        effective_mode = "chat"
        mode_state = {"state": "chat", "tool_mode": "chat"}
    else:
        from server.sessions import resolve_chat_mode

        mode_state = resolve_chat_mode(user_id, session_id, req.mode)
        effective_mode = mode_state["tool_mode"]

    request_id = req.request_id or str(uuid.uuid4())
    lock_token = runtime_store.acquire_conversation(session_id)
    if lock_token is None:
        raise HTTPException(status_code=409, detail="session already has a running response")
    try:
        repository.create_model_run(request_id, user_id, session_id)
    except IntegrityError as exc:
        runtime_store.release_conversation(session_id, lock_token)
        raise HTTPException(status_code=409, detail="request_id already exists") from exc

    repository.update_conversation(user_id, session_id, status="running")
    runtime_store.mark_request(request_id, "running")
    snapshot_started_at = time.time()
    run_started_at = time.monotonic()
    runtime_store.save_chat_snapshot(
        request_id,
        session_id,
        "",
        0,
        started_at=snapshot_started_at,
    )

    event_queue: queue.Queue[object] = queue.Queue()
    sentinel = object()
    event_sequence = 0
    event_lock = threading.Lock()
    snapshot_lock = threading.Lock()
    snapshot_chunks: list[str] = []
    snapshot_last_saved_at = run_started_at
    stream_attached = threading.Event()
    stream_attached.set()
    agent_holder: dict[str, object] = {}

    def emit(event: str, data: dict) -> int:
        nonlocal event_sequence
        with event_lock:
            event_sequence += 1
            event_id = event_sequence
        if event != "delta":
            runtime_store.append_event(request_id, event_id, event, data)
        if stream_attached.is_set():
            event_queue.put(
                {
                    "id": str(event_id),
                    "event": event,
                    "data": json.dumps(data, ensure_ascii=False),
                }
            )
        return event_id

    def on_delta(chunk: str) -> None:
        nonlocal snapshot_last_saved_at
        if runtime_store.is_cancelled(request_id):
            interrupt = getattr(agent_holder.get("agent"), "interrupt", None)
            if callable(interrupt):
                interrupt("cancelled by user")
            return
        if chunk:
            event_id = emit("delta", {"content": chunk, "request_id": request_id})
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
                    event_id,
                    started_at=snapshot_started_at,
                )

    def run_agent() -> None:
        runtime_metadata: dict = {"mode": effective_mode}
        try:
            from server.agent_factory import build_agent
            from server.audit import record_event
            from server.memory import save_memory_candidate

            prior_messages = repository.get_messages(session_id)
            history = [
                {"role": message["role"], "content": message["content"]}
                for message in prior_messages
            ]
            title = repository.get_owned_conversation(user_id, session_id)["title"]
            if not prior_messages:
                title = " ".join(req.message.split()).strip()[:40] or title
                repository.update_conversation(user_id, session_id, title=title)

            user_message = repository.append_message(session_id, "user", req.message)
            emit(
                "session",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "title": title,
                    "message": user_message,
                },
            )

            agent = build_agent(
                session_id=session_id,
                user_id=user_id,
                prefill_messages=history,
                mode=effective_mode,
            )
            agent_holder["agent"] = agent
            with _active_agents_lock:
                _active_agents[request_id] = (user_id, agent)

            runtime_metadata = _agent_runtime_metadata(agent, effective_mode)
            runtime_metadata["plan_state"] = mode_state.get("state")
            model_config = {
                "provider": runtime_metadata.get("provider"),
                "reasoning_config": runtime_metadata.get("reasoning_config"),
                "enabled_toolsets": runtime_metadata.get("enabled_toolsets"),
                "mode": runtime_metadata.get("mode"),
                "plan_state": runtime_metadata.get("plan_state"),
            }
            repository.update_conversation_model(
                session_id,
                model=runtime_metadata.get("model"),
                model_config=model_config,
            )
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status="started",
                mode=effective_mode,
                metadata={**runtime_metadata, "request_id": request_id},
            )

            final = agent.chat(req.message, stream_callback=on_delta) or ""
            cancelled = runtime_store.is_cancelled(request_id)
            assistant_status = "cancelled" if cancelled else "completed"
            duration_ms = max(0, int((time.monotonic() - run_started_at) * 1000))
            assistant_message = repository.append_message(
                session_id,
                "assistant",
                final,
                status=assistant_status,
                model_run_id=request_id,
                duration_ms=duration_ms,
            )
            if final and not cancelled:
                try:
                    save_memory_candidate(user_id, session_id, req.message, final)
                except Exception:
                    logger.debug("Could not save memory candidate", exc_info=True)

            final_status = "cancelled" if cancelled else "completed"
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status=final_status,
                mode=effective_mode,
                metadata={
                    **runtime_metadata,
                    "request_id": request_id,
                    "response_chars": len(final),
                },
            )
            repository.finish_model_run(
                request_id,
                status=final_status,
                provider=runtime_metadata.get("provider"),
                model=runtime_metadata.get("model"),
            )
            repository.update_conversation(user_id, session_id, status="idle")
            final_event_id = emit(
                "final",
                {
                    "content": final,
                    "message": assistant_message,
                    "session_id": session_id,
                    "request_id": request_id,
                    "title": title,
                    "status": final_status,
                },
            )
            runtime_store.save_chat_snapshot(
                request_id,
                session_id,
                final,
                final_event_id,
                status=final_status,
                started_at=snapshot_started_at,
                ttl_seconds=300,
            )
        except Exception as exc:
            logger.exception("Chat request %s failed", request_id)
            error_text = f"{type(exc).__name__}: {exc}"
            try:
                from server.audit import record_event

                record_event(
                    event_type="chat_turn",
                    session_id=session_id,
                    user_id=user_id,
                    status="failed",
                    mode=effective_mode,
                    metadata={"request_id": request_id, "plan_state": mode_state.get("state")},
                    error=error_text,
                )
                repository.finish_model_run(request_id, status="failed", error=error_text)
                repository.update_conversation(user_id, session_id, status="idle")
            except Exception:
                logger.exception("Failed to persist Chat failure state")
            error_event_id = emit(
                "error",
                {
                    "message": "回答生成失败，请稍后重试",
                    "code": type(exc).__name__,
                    "request_id": request_id,
                },
            )
            with snapshot_lock:
                partial_content = "".join(snapshot_chunks)
            runtime_store.save_chat_snapshot(
                request_id,
                session_id,
                partial_content,
                error_event_id,
                status="failed",
                started_at=snapshot_started_at,
                ttl_seconds=300,
            )
        finally:
            with _active_agents_lock:
                _active_agents.pop(request_id, None)
            runtime_store.mark_request(request_id, "done")
            runtime_store.release_conversation(session_id, lock_token)
            if stream_attached.is_set():
                event_queue.put(sentinel)

    threading.Thread(target=run_agent, daemon=True, name=f"chat-{request_id[:8]}").start()

    async def event_gen():
        try:
            while True:
                if await request.is_disconnected():
                    stream_attached.clear()
                    break
                try:
                    item = await asyncio.to_thread(event_queue.get, True, 0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    emit_data = {
                        "session_id": session_id,
                        "user_id": user_id,
                        "request_id": request_id,
                    }
                    yield {
                        "id": str(event_sequence + 1),
                        "event": "done",
                        "data": json.dumps(emit_data, ensure_ascii=False),
                    }
                    break
                yield item
                if await request.is_disconnected():
                    stream_attached.clear()
                    break
        finally:
            stream_attached.clear()

    return EventSourceResponse(event_gen())


@router.post("/chat/{request_id}/cancel", status_code=202)
def cancel_chat(request_id: str, user: dict = Depends(get_current_user)):
    run = get_repository().get_owned_model_run(user["id"], request_id)
    if run is None:
        raise HTTPException(status_code=404, detail="running request not found")
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail="request is not running")
    get_runtime_store().cancel_request(request_id)
    interrupted_locally = _interrupt_local_agent(request_id, user["id"])
    return {
        "request_id": request_id,
        "status": "cancelling",
        "interrupted_locally": interrupted_locally,
    }
