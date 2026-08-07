"""Build AIAgent instances for the headless server."""
from __future__ import annotations

import os


def build_agent(
    *,
    session_id: str,
    user_id: str,
    prefill_messages=None,
    mode: str = None,
    permission_mode: str = "read",
    tool_progress_callback=None,
    tool_start_callback=None,
    tool_complete_callback=None,
    status_callback=None,
    event_callback=None,
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
        mode: "chat" | "plan" | "execute" | None.
    """
    os.environ.setdefault("HERMES_HEADLESS", "1")

    from run_agent import AIAgent
    from server.memory import list_memory_contents
    from server.features import get_features
    from server.runtime_config import load_runtime_config
    from server.tool_policy import resolve_toolsets

    is_plan = mode == "plan"
    is_chat = mode == "chat"
    features = get_features()
    runtime_config = load_runtime_config()
    # Extension tools remain disabled until each deployment tool declares a
    # trustworthy risk level and passes the task permission policy.
    toolsets = resolve_toolsets(
        mode=mode,
        features=features,
        permission_mode=permission_mode,
    )

    # Build an ephemeral system-prompt section combining persistent memory
    # (per-user, loaded fresh each request) and the plan-mode instruction.
    parts = [
        "You are Cortex Agent, an enterprise AI assistant. Use the Cortex Agent "
        "name in all user-facing identity references. Hermes is the internal "
        "framework name and must not be presented as the product name."
    ]
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
            "approve to switch to EXECUTE mode. The EXECUTE phase can use a Docker-sandboxed "
            "terminal when the user grants full task permission. You cannot call that terminal "
            "during PLAN mode, but you may include necessary terminal steps in the plan."
        )
    if is_chat:
        parts.append(
            "You are in CHAT mode. Answer questions and investigate only with the supplied "
            "read-only tools. Never modify files, databases, external systems, or shared "
            "resources. If the request requires a side effect, explain that it must be "
            "continued as an Agent task."
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
        tool_progress_callback=tool_progress_callback,
        tool_start_callback=tool_start_callback,
        tool_complete_callback=tool_complete_callback,
        status_callback=status_callback,
        event_callback=event_callback,
    )
