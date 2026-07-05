from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from server.agent_factory import build_agent


def _write_config(home: Path, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


def test_build_agent_passes_runtime_config_and_execute_tool_policy(
    monkeypatch,
    tmp_path,
) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
        """
model:
  provider: custom
  default: llama-3.3
agent:
  reasoning_effort: low
features:
  computer_use: true
""",
    )
    captured = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=CapturingAgent))
    import server.memory as memory

    monkeypatch.setattr(memory, "list_memory_contents", lambda _user_id: [])

    build_agent(session_id="s1", user_id="u1", mode="execute")

    assert captured["provider"] == "custom"
    assert captured["model"] == "llama-3.3"
    assert captured["reasoning_config"] == {"enabled": True, "effort": "low"}
    assert captured["enabled_toolsets"] == ["db", "terminal", "computer_use"]


def test_build_agent_plan_mode_removes_terminal_toolset(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
        """
model: deepseek-v4-pro
features:
  computer_use: true
""",
    )
    captured = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=CapturingAgent))
    import server.memory as memory

    monkeypatch.setattr(memory, "list_memory_contents", lambda _user_id: ["remember this"])

    build_agent(session_id="s1", user_id="u1", mode="plan")

    assert captured["enabled_toolsets"] == ["db"]
    assert "PLAN mode" in captured["ephemeral_system_prompt"]
    assert "remember this" in captured["ephemeral_system_prompt"]
