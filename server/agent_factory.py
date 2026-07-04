"""Build AIAgent instances for the headless server.

Inc 1: minimal DeepSeek config, thinking off, no memory/tools — proves the
AIAgent-in-FastAPI + SSE streaming integration. Later increments read
provider/model from config.yaml and add toolsets, memory, per-user scoping.
"""
from __future__ import annotations

from typing import Callable, Optional

# Inc 1 hardcoded dev config (DeepSeek, thinking off).
# TODO(Inc 2): read provider/model/reasoning from config.yaml.
_PROVIDER = "deepseek"
_MODEL = "deepseek-v4-pro"
_REASONING_CONFIG = {"enabled": False}  # thinking off


def build_agent(
    *,
    session_id: str,
    user_id: str,
    prefill_messages=None,
):
    """Construct an AIAgent scoped to one server-side chat session.

    Streaming is handled per-call via ``AIAgent.chat(message,
    stream_callback=...)`` — do NOT also pass ``stream_delta_callback`` here,
    or each delta fires twice (one per hook).

    Persistence is managed EXPLICITLY by the caller (routes/chat.py creates
    the session row + appends messages), NOT via AIAgent.session_db — the
    agent's deferred-row/close-finalize semantics don't fit a per-request
    agent. Resume history is passed in via ``prefill_messages``.

    Args:
        session_id: unique session id.
        user_id: authenticated user id.
        prefill_messages: prior turns (OpenAI format) for session resume.
    """
    from run_agent import AIAgent

    return AIAgent(
        provider=_PROVIDER,
        model=_MODEL,
        reasoning_config=_REASONING_CONFIG,
        session_id=session_id,
        user_id=user_id,
        platform="headless",
        enabled_toolsets=["db"],    # Inc 3.3: db_query tool (read-only, DB-grant enforced)
        skip_memory=True,           # no memory provider yet (Step 3.4)
        skip_context_files=True,
        quiet_mode=True,
        prefill_messages=prefill_messages,  # Inc 3: resume prior history
    )
