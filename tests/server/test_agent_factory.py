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
    assert captured["enabled_toolsets"] == ["db", "session_search", "terminal"]


def test_build_agent_plan_mode_removes_terminal_toolset(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
"""
model: deepseek-v4-pro
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

    assert captured["enabled_toolsets"] == ["db", "session_search"]
    assert "PLAN mode" in captured["ephemeral_system_prompt"]
    assert "remember this" in captured["ephemeral_system_prompt"]


def test_build_agent_adds_enabled_deployment_mcp_toolsets(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "deployment.yaml").write_text(
        """
mcp_servers:
  - name: metrics
    url: http://metrics.example/sse
    enabled: true
  - name: disabled-one
    url: http://disabled.example/sse
    enabled: false
""",
        encoding="utf-8",
    )
    captured = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=CapturingAgent))
    import server.memory as memory
    import server.mcp as mcp

    registered = []
    monkeypatch.setattr(memory, "list_memory_contents", lambda _user_id: [])
    monkeypatch.setattr(mcp, "register_deployment_mcp_servers", lambda: registered.append(True) or [])

    build_agent(session_id="s1", user_id="u1", mode="execute")

    assert registered == [True]
    assert "mcp-metrics" in captured["enabled_toolsets"]
    assert "mcp-disabled-one" not in captured["enabled_toolsets"]
