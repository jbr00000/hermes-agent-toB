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
):
    """Construct an AIAgent scoped to one server-side chat session.

    Streaming is handled per-call via ``AIAgent.chat(message,
    stream_callback=...)`` — do NOT also pass ``stream_delta_callback`` here,
    or each delta fires twice (one per hook).

    Args:
        session_id: unique session id (persisted via SessionDB in later incs).
        user_id: authenticated user id (Inc 1: stub "dev-user"; Inc 2: JWT user).
    """
    from run_agent import AIAgent

    return AIAgent(
        provider=_PROVIDER,
        model=_MODEL,
        reasoning_config=_REASONING_CONFIG,
        session_id=session_id,
        user_id=user_id,
        platform="headless",
        enabled_toolsets=[],        # Inc 1: pure chat, no tools
        skip_memory=True,           # no memory provider yet (Step 3.4)
        skip_context_files=True,
        quiet_mode=True,
    )
