from __future__ import annotations

from server.tool_policy import resolve_toolsets


def test_execute_mode_allows_sandbox_terminal_by_default() -> None:
    assert resolve_toolsets(mode="execute", features={}) == ["db", "terminal"]


def test_plan_mode_is_read_only_even_when_elevated_features_are_enabled() -> None:
    assert resolve_toolsets(
        mode="plan",
        features={"computer_use": True, "host_terminal": True},
    ) == ["db"]


def test_execute_mode_adds_computer_use_only_when_enabled() -> None:
    assert resolve_toolsets(mode="execute", features={"computer_use": True}) == [
        "db",
        "terminal",
        "computer_use",
    ]
