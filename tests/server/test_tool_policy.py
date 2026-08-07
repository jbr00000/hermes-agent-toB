from __future__ import annotations

from server.tool_policy import resolve_toolsets


def test_chat_mode_is_read_only() -> None:
    assert resolve_toolsets(mode="chat", features={}) == ["db"]


def test_execute_mode_defaults_to_read_only() -> None:
    assert resolve_toolsets(mode="execute", features={}) == ["db"]


def test_plan_mode_is_read_only_even_when_elevated_features_are_enabled() -> None:
    assert resolve_toolsets(
        mode="plan",
        features={"host_terminal": True},
        permission_mode="full",
    ) == ["db"]


def test_execute_mode_requires_full_permission_for_sandbox_terminal() -> None:
    assert resolve_toolsets(
        mode="execute",
        features={"host_terminal": False},
        permission_mode="full",
    ) == ["db", "terminal"]


def test_controlled_permission_does_not_enable_unclassified_write_tools() -> None:
    assert resolve_toolsets(
        mode="execute",
        features={},
        permission_mode="controlled",
    ) == ["db"]
