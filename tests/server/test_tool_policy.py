from __future__ import annotations

from server.tool_policy import resolve_toolsets


def test_chat_mode_is_read_only() -> None:
    assert resolve_toolsets(mode="chat", features={}) == ["db", "web", "knowledge"]


def test_execute_mode_defaults_to_read_only() -> None:
    assert resolve_toolsets(mode="execute", features={}) == ["db", "web", "knowledge"]


def test_plan_mode_is_read_only_even_when_elevated_features_are_enabled() -> None:
    assert resolve_toolsets(
        mode="plan",
        features={"host_terminal": True},
        permission_mode="full",
    ) == ["db", "web", "knowledge"]


def test_knowledge_mode_only_gets_the_knowledge_toolset() -> None:
    """知识库问答模式：只挂 knowledge_search，由 RAG prompt 约束先检索再作答。"""
    assert resolve_toolsets(mode="knowledge", features={}) == ["knowledge"]
    assert resolve_toolsets(
        mode="knowledge", features={}, permission_mode="full"
    ) == ["knowledge"]


def test_execute_mode_requires_full_permission_for_sandbox_terminal() -> None:
    assert resolve_toolsets(
        mode="execute",
        features={"host_terminal": False},
        permission_mode="full",
    ) == ["db", "terminal", "web", "knowledge"]


def test_controlled_permission_enables_terminal_behind_the_approval_gate() -> None:
    """controlled 拿到 terminal 工具集；每条命令由 tool_gate 审批钩子逐条把关。"""
    assert resolve_toolsets(
        mode="execute",
        features={},
        permission_mode="controlled",
    ) == ["db", "terminal", "web", "knowledge"]


def test_browser_toolset_requires_flag_and_full_permission() -> None:
    """P3 浏览器兜底：flag 与 full 权限双闸门，缺一不出现；chat/plan 永远不给。"""
    # flag 关 + full → 无 browser
    assert resolve_toolsets(
        mode="execute", features={}, permission_mode="full"
    ) == ["db", "terminal", "web", "knowledge"]
    # flag 开 + controlled → 无 browser（浏览器仍属 full 专属）
    assert resolve_toolsets(
        mode="execute",
        features={"browser_automation": True},
        permission_mode="controlled",
    ) == ["db", "terminal", "web", "knowledge"]
    # flag 开 + full → browser 放行
    assert resolve_toolsets(
        mode="execute",
        features={"browser_automation": True},
        permission_mode="full",
    ) == ["db", "terminal", "web", "knowledge", "browser"]
    # chat / plan 即使 flag 开 + full 也不给浏览器（规划阶段不允许副作用）
    assert resolve_toolsets(
        mode="chat",
        features={"browser_automation": True},
        permission_mode="full",
    ) == ["db", "web", "knowledge"]
    assert resolve_toolsets(
        mode="plan",
        features={"browser_automation": True},
        permission_mode="full",
    ) == ["db", "web", "knowledge"]
