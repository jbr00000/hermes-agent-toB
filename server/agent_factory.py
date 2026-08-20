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
    knowledge_kb_id: str | None = None,
    knowledge_kb_name: str | None = None,
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

    knowledge mode: 知识库问答。只挂 knowledge 工具集（tool_policy 决定），
    此处注入 RAG 约束 prompt；``knowledge_kb_id`` 非空时把检索限定到该库
    （prompt 钉住 + 工具侧校验 kb 存在；模型漏传时退化为全库检索）。

    Args:
        session_id: unique session id.
        user_id: authenticated user id.
        prefill_messages: prior turns (OpenAI format) for session resume.
        mode: "chat" | "knowledge" | "plan" | "execute" | None.
    """
    os.environ.setdefault("HERMES_HEADLESS", "1")

    from run_agent import AIAgent
    from server.memory import list_memory_contents
    from server.features import get_features
    from server.runtime_config import load_runtime_config
    from server.tool_policy import resolve_toolsets

    is_plan = mode == "plan"
    is_chat = mode == "chat"
    is_knowledge = mode == "knowledge"
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
    if mode == "execute":
        parts.append(
            "当前是执行模式，终端与文件工具运行在 Docker 沙箱内。交付物规则：\n"
            "1. 需要交付给用户的文件（Excel、Word、CSV、报告等）一律写入当前工作目录"
            "（即本任务在沙箱工作区中的专属目录），使用清晰的文件名（可以用中文，"
            "如「费用测算结果.xlsx」），不要写入其他目录。\n"
            "2. 不要尝试把文件复制到宿主机或用户的桌面/下载目录——沙箱无法也不应"
            "访问宿主文件系统；用户在 Web 界面的「交付文件」卡片中下载产物。\n"
            "3. 完成后在回复中列出交付的文件名，并说明用户可在界面上下载。"
        )
    if is_knowledge:
        parts.append(
            "当前是知识库问答模式。规则：\n"
            "1. 用户问题的末尾会附带知识库检索结果（带编号的候选分块）或检索提示："
            "附带检索结果时，严格基于这些分块回答，不得使用自有知识补充事实；"
            "附带检索提示时，按提示表述调用 knowledge_search 检索后再回答；"
            "确需补充检索时，也可自行调用 knowledge_search。\n"
            "2. 引用来源时在句末标注【N】，N 为分块编号。"
            "一个事实来自多个分块时可连标，如【1】【3】。\n"
            "3. 检索结果为空或与问题不相关时，明确回答「知识库中未找到相关内容」，"
            "不得编造。\n"
            "4. 多个分块内容冲突时，指出差异并分别标注来源，不要擅自取舍。\n"
            "5. 用中文回答，先给结论再给依据。"
        )
        if knowledge_kb_id:
            label = knowledge_kb_name or knowledge_kb_id
            parts.append(
                f'用户选择了知识库「{label}」：调用 knowledge_search 时必须传 '
                f'kb_id="{knowledge_kb_id}"，不要检索其他库。'
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
