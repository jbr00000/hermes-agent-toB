from __future__ import annotations

from pathlib import Path

from server.runtime_config import load_runtime_config


def _write_config(home: Path, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


def test_runtime_config_reads_string_model_and_infers_deepseek(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
        """
model: deepseek-v4-pro
agent:
  reasoning_effort: none
""",
    )

    runtime = load_runtime_config()

    assert runtime.provider == "deepseek"
    assert runtime.model == "deepseek-v4-pro"
    assert runtime.reasoning_config == {"enabled": False}


def test_runtime_config_reads_dict_model_with_explicit_provider(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
        """
model:
  provider: custom
  default: llama-3.3
  base_url: https://llm.example.test/v1
agent:
  reasoning_effort: high
""",
    )

    runtime = load_runtime_config()

    assert runtime.provider == "custom"
    assert runtime.model == "llama-3.3"
    assert runtime.reasoning_config == {"enabled": True, "effort": "high"}


def test_runtime_config_infers_kept_provider_from_model_prefix(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(home, "model: qwen-plus\n")

    runtime = load_runtime_config()

    assert runtime.provider == "alibaba"
    assert runtime.model == "qwen-plus"


def test_runtime_config_accepts_kimi_coding_provider(monkeypatch, tmp_path) -> None:
    """kimi-coding 在受支持名单内：显式声明不被回落成 deepseek。"""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(
        home,
        """
model:
  default: kimi-k2.5
  provider: kimi-coding
  max_input_tokens: 131072
""",
    )

    runtime = load_runtime_config()

    assert runtime.provider == "kimi-coding"
    assert runtime.model == "kimi-k2.5"
    assert runtime.max_input_tokens == 131072


def test_runtime_config_infers_kimi_coding_from_model_prefix(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_config(home, "model: kimi-k2.5\n")

    runtime = load_runtime_config()

    assert runtime.provider == "kimi-coding"
    assert runtime.model == "kimi-k2.5"


def test_runtime_config_falls_back_to_safe_default_without_model(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    runtime = load_runtime_config()

    assert runtime.provider == "deepseek"
    assert runtime.model == "deepseek-v4-pro"
    assert runtime.reasoning_config == {"enabled": False}
