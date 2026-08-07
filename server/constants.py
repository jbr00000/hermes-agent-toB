from __future__ import annotations

DEFAULT_CHAT_TITLE = "新问答"
DEFAULT_AGENT_TASK_TITLE = "新任务"
LEGACY_DEFAULT_AGENT_TASK_TITLES = frozenset(
    {DEFAULT_AGENT_TASK_TITLE, "New agent task", "新智能体任务"}
)


def is_default_agent_task_title(title: str | None) -> bool:
    return (title or "").strip() in LEGACY_DEFAULT_AGENT_TASK_TITLES
