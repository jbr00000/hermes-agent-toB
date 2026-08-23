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

    callback = lambda *args: None
    build_agent(
        session_id="s1",
        user_id="u1",
        mode="execute",
        permission_mode="full",
        tool_start_callback=callback,
    )

    assert captured["provider"] == "custom"
    assert captured["model"] == "llama-3.3"
    assert captured["reasoning_config"] == {"enabled": True, "effort": "low"}
    assert captured["enabled_toolsets"] == ["db", "terminal", "file", "web", "knowledge"]
    assert captured["tool_start_callback"] is callback


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

    assert captured["enabled_toolsets"] == ["db", "web", "knowledge"]
    assert "PLAN mode" in captured["ephemeral_system_prompt"]
    assert "Docker-sandboxed terminal" in captured["ephemeral_system_prompt"]
    assert "remember this" in captured["ephemeral_system_prompt"]


def test_build_agent_knowledge_disabled_strips_toolset(monkeypatch, tmp_path) -> None:
    """运行级 knowledge_enabled=False：knowledge 工具集被摘除，模型看不到 knowledge_search。"""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    captured = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=CapturingAgent))
    import server.memory as memory

    monkeypatch.setattr(memory, "list_memory_contents", lambda _user_id: [])

    build_agent(session_id="s1", user_id="u1", mode="plan", knowledge_enabled=False)

    assert "knowledge" not in captured["enabled_toolsets"]
    assert "db" in captured["enabled_toolsets"]  # 其余只读工具集不受影响


def test_build_agent_scoped_kb_adds_prompt_constraint(monkeypatch, tmp_path) -> None:
    """plan/execute 指定 kb_id：工具集照挂，prompt 追加选库限定（与 knowledge 模式同措辞）。"""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    captured = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=CapturingAgent))
    import server.memory as memory

    monkeypatch.setattr(memory, "list_memory_contents", lambda _user_id: [])

    build_agent(
        session_id="s1",
        user_id="u1",
        mode="execute",
        knowledge_kb_id="kb-7",
        knowledge_kb_name="财务制度",
    )

    assert "knowledge" in captured["enabled_toolsets"]
    prompt = captured["ephemeral_system_prompt"]
    assert 'kb_id="kb-7"' in prompt
    assert "财务制度" in prompt


def test_build_agent_defers_deployment_mcp_toolsets(monkeypatch, tmp_path) -> None:
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

    assert registered == []
    assert captured["enabled_toolsets"] == ["db", "web", "knowledge"]
