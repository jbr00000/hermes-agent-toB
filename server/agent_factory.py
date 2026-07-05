"""Build AIAgent instances for the headless server."""
from __future__ import annotations

import os


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
    changes.

    Args:
        session_id: unique session id.
        user_id: authenticated user id.
        prefill_messages: prior turns (OpenAI format) for session resume.
        mode: "plan" | "execute" | None.
    """
    os.environ.setdefault("HERMES_HEADLESS", "1")

    from run_agent import AIAgent
    from server.memory import list_memory_contents
    from server.features import get_features
    from server.mcp import enabled_mcp_toolsets, register_deployment_mcp_servers
    from server.runtime_config import load_runtime_config
    from server.tool_policy import resolve_toolsets

    is_plan = mode == "plan"
    features = get_features()
    runtime_config = load_runtime_config()
    mcp_toolsets = enabled_mcp_toolsets()
    if mcp_toolsets:
        register_deployment_mcp_servers()
    toolsets = resolve_toolsets(mode=mode, features=features) + mcp_toolsets

    # Build an ephemeral system-prompt section combining persistent memory
    # (per-user, loaded fresh each request) and the plan-mode instruction.
    parts = []
    memories = list_memory_contents(user_id)
    if memories:
        parts.append(
            "Persistent memory about this user (carries across sessions):\n"
            + "\n".join(f"- {m}" for m in memories)
        )
    if is_plan:
        parts.append(
            "You are in PLAN mode. Investigate with read-only tools, then produce a clear, "
            "structured plan for the user to approve. Do NOT execute any changes — no writes, "
            "no mutations, no long-running actions. End with a concrete step list the user can "
            "approve to switch to EXECUTE mode."
        )
    ephemeral = "\n\n".join(parts) if parts else None

    return AIAgent(
        provider=runtime_config.provider,
        model=runtime_config.model,
        reasoning_config=runtime_config.reasoning_config,
        session_id=session_id,
        user_id=user_id,
        platform="headless",
        # db = read-only queries; terminal = sandboxed shell/code execution
        # (docker-only since Step 2.4; TERMINAL_ENV=docker + TERMINAL_DOCKER_IMAGE
        # select the sandbox container). execute_code (PTC) added when needed.
        enabled_toolsets=toolsets,
        skip_memory=True,           # AIAgent's memory-provider system is off; server injects memory above
        skip_context_files=True,
        quiet_mode=True,
        prefill_messages=prefill_messages,
        ephemeral_system_prompt=ephemeral,
    )
