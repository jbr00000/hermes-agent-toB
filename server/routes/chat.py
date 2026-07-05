"""POST /chat — SSE-streaming chat endpoint (authenticated).

Inc 2: requires a JWT (Authorization: Bearer); the authenticated user_id scopes
the AIAgent. Inc 3 also persists sessions per user via SessionDB.

The AIAgent runs synchronously in a worker thread; text deltas are pushed onto
a queue that the async SSE generator drains.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from server.deps import get_current_user

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: Optional[str] = None  # "plan" | "execute" (default: execute)


def _agent_runtime_metadata(agent, mode: str | None) -> dict:
    return {
        "provider": getattr(agent, "provider", None),
        "model": getattr(agent, "model", None),
        "reasoning_config": getattr(agent, "reasoning_config", None),
        "enabled_toolsets": list(getattr(agent, "enabled_toolsets", None) or []),
        "mode": (mode or "execute"),
    }


@router.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    # Inc 3: if resuming an existing session, it must belong to this user.
    session_id = req.session_id or str(uuid.uuid4())
    user_id = user["id"]  # per-user scoping (decision 2/3)
    if req.session_id:
        from server.sessions import assert_session_owned

        assert_session_owned(user["id"], req.session_id)  # 403 if exists + not owned

    from server.sessions import resolve_chat_mode

    mode_state = resolve_chat_mode(user_id, session_id, req.mode)
    effective_mode = mode_state["tool_mode"]

    q: "queue.Queue" = queue.Queue()
    SENTINEL = object()

    def on_delta(chunk: str) -> None:
        q.put(("delta", chunk))

    def run_agent() -> None:
        try:
            from server.agent_factory import build_agent
            from server.audit import record_event
            from server.sessions import get_session_db

            db = get_session_db()
            # Inc 3: ensure the session row exists (server-managed persistence).
            if db.get_session(session_id) is None:
                db.create_session(session_id, "headless", user_id=user_id, chat_id=session_id)
            # Load prior turns for resume (empty for a brand-new session).
            try:
                history = db.get_messages_as_conversation(session_id)
            except Exception:
                history = None

            agent = build_agent(
                session_id=session_id,
                user_id=user_id,
                prefill_messages=history,
                mode=effective_mode,
            )
            runtime_metadata = _agent_runtime_metadata(agent, effective_mode)
            runtime_metadata["plan_state"] = mode_state.get("state")
            model_config = {
                "provider": runtime_metadata.get("provider"),
                "reasoning_config": runtime_metadata.get("reasoning_config"),
                "enabled_toolsets": runtime_metadata.get("enabled_toolsets"),
                "mode": runtime_metadata.get("mode"),
                "plan_state": runtime_metadata.get("plan_state"),
            }
            db.create_session(
                session_id,
                "headless",
                user_id=user_id,
                chat_id=session_id,
                model=runtime_metadata.get("model"),
                model_config=model_config,
            )
            db.update_session_meta(
                session_id,
                json.dumps(model_config, ensure_ascii=False),
                model=runtime_metadata.get("model"),
            )
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status="started",
                mode=runtime_metadata.get("mode"),
                metadata=runtime_metadata,
            )
            final = agent.chat(req.message, stream_callback=on_delta)

            # Persist this turn so the next request can resume it.
            db.append_message(session_id, "user", content=req.message)
            db.append_message(session_id, "assistant", content=final or "")
            try:
                from server.memory import save_memory_candidate

                save_memory_candidate(user_id, session_id, req.message, final or "")
            except Exception:
                pass
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status="completed",
                mode=runtime_metadata.get("mode"),
                metadata={**runtime_metadata, "response_chars": len(final or "")},
            )
            q.put(("final", final or ""))
        except Exception as exc:  # surfaced to the client as an error event
            try:
                from server.audit import record_event

                record_event(
                    event_type="chat_turn",
                    session_id=session_id,
                    user_id=user_id,
                    status="failed",
                    mode=effective_mode,
                    metadata={"mode": effective_mode, "plan_state": mode_state.get("state")},
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            q.put(SENTINEL)

    threading.Thread(target=run_agent, daemon=True).start()

    async def event_gen():
        while True:
            item = q.get()
            if item is SENTINEL:
                yield {"event": "done", "data": json.dumps({"session_id": session_id, "user_id": user_id})}
                break
            kind, payload = item
            yield {"event": kind, "data": json.dumps({"content": payload})}

    return EventSourceResponse(event_gen())
