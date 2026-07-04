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


@router.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    # Inc 3: if resuming an existing session, it must belong to this user.
    if req.session_id:
        from server.sessions import assert_session_owned

        assert_session_owned(user["id"], req.session_id)  # 403 if exists + not owned

    session_id = req.session_id or str(uuid.uuid4())
    user_id = user["id"]  # per-user scoping (decision 2/3)

    q: "queue.Queue" = queue.Queue()
    SENTINEL = object()

    def on_delta(chunk: str) -> None:
        q.put(("delta", chunk))

    def run_agent() -> None:
        try:
            from server.agent_factory import build_agent
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
                mode=req.mode,
            )
            final = agent.chat(req.message, stream_callback=on_delta)

            # Persist this turn so the next request can resume it.
            db.append_message(session_id, "user", content=req.message)
            db.append_message(session_id, "assistant", content=final or "")
            q.put(("final", final or ""))
        except Exception as exc:  # surfaced to the client as an error event
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
