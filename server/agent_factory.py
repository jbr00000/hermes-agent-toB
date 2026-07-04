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
    mode: str = None,
):
    """Construct an AIAgent scoped to one server-side chat session.

    Streaming is handled per-call via ``AIAgent.chat(message,
    stream_callback=...)`` — do NOT also pass ``stream_delta_callback`` here,
    or each delta fires twice (one per hook).

    Persistence is managed EXPLICITLY by the caller (routes/chat.py creates
    the session row + appends messages), NOT via AIAgent.session_db — the
    agent's deferred-row/close-finalize semantics don't fit a per-request
    agent. Resume history is passed in via ``prefill_messages``.

    plan mode (decision 9): when mode=="plan", the agent is given a read-only
    toolset + a system-prompt instructing it to produce a plan and NOT execute
    changes. The toolset restriction becomes meaningful once mutating tools
    (terminal/execute_code/write — Step 2.4 sandbox) are enabled; until then
    db_query is the only tool and is already read-only, so the prompt carries
    the plan-mode behavior.

    Args:
        session_id: unique session id.
        user_id: authenticated user id.
        prefill_messages: prior turns (OpenAI format) for session resume.
        mode: "plan" | "execute" | None.
    """
    from run_agent import AIAgent

    is_plan = mode == "plan"
    plan_prompt = (
        "You are in PLAN mode. Investigate with read-only tools, then produce a clear, "
        "structured plan for the user to approve. Do NOT execute any changes — no writes, "
        "no mutations, no long-running actions. End with a concrete step list the user can "
        "approve to switch to EXECUTE mode."
    ) if is_plan else None

    return AIAgent(
        provider=_PROVIDER,
        model=_MODEL,
        reasoning_config=_REASONING_CONFIG,
        session_id=session_id,
        user_id=user_id,
        platform="headless",
        enabled_toolsets=["db"],    # read-only (plan mode keeps this; execute adds mutating tools in Step 2.4)
        skip_memory=True,
        skip_context_files=True,
        quiet_mode=True,
        prefill_messages=prefill_messages,
        ephemeral_system_prompt=plan_prompt,
    )
