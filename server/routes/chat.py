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


@router.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    session_id = req.session_id or str(uuid.uuid4())
    user_id = user["id"]  # per-user scoping (decision 2/3)

    q: "queue.Queue" = queue.Queue()
    SENTINEL = object()

    def on_delta(chunk: str) -> None:
        q.put(("delta", chunk))

    def run_agent() -> None:
        try:
            from server.agent_factory import build_agent

            agent = build_agent(session_id=session_id, user_id=user_id)
            final = agent.chat(req.message, stream_callback=on_delta)
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
