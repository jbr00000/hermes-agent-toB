"""问数 LLMClient 的协议分派与 deployment.yaml nl2sql 段覆盖测试。

真机网络一律不打：chat_completions 路径构造完客户端后整体替换为录制假客户端；
anthropic_messages 路径只替换 ``build_anthropic_client`` 返回的原生 Anthropic
客户端，中间的 build_anthropic_kwargs 翻译与 normalize_response 规范化都走真代码。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _write_config_yaml(text: str) -> None:
    from hermes_constants import get_hermes_home

    (get_hermes_home() / "config.yaml").write_text(text.strip(), encoding="utf-8")


def _write_deployment_yaml(tmp_path, monkeypatch, text: str) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(text.strip(), encoding="utf-8")
    monkeypatch.setenv("HERMES_DEPLOYMENT_CONFIG", str(path))


def _use_custom_qwen_global() -> None:
    """全局主模型 = LAN custom/qwen（chat_completions），问数默认应跟随它。"""
    _write_config_yaml(
        """
model:
  default: qwen-test
  provider: custom
  base_url: http://lan-llm.internal/v1
"""
    )


class _RecordingCompletions:
    """OpenAI 形状假客户端：记录 create 入参并返回固定文本。"""

    def __init__(self, text: str = "SELECT 1") -> None:
        self.kwargs: dict | None = None
        self._text = text

    async def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=self._text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _record_client(client) -> _RecordingCompletions:
    completions = _RecordingCompletions()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return completions


class _FakeAnthropicStream:
    """messages.stream() 的同步上下文管理器 + get_final_message() 契约。"""

    def __init__(self, response) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


class _FakeAnthropicClient:
    """原生 Anthropic 客户端形状：捕捉 messages.stream 收到的翻译后 kwargs。"""

    def __init__(self, text: str = "<result>ok</result>") -> None:
        self.captured: dict | None = None
        block = SimpleNamespace(type="text", text=text)
        response = SimpleNamespace(
            content=[block],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=3, output_tokens=5),
        )
        self.messages = SimpleNamespace(
            stream=lambda **kwargs: self._stream(kwargs, response)
        )

    def _stream(self, kwargs, response):
        self.captured = kwargs
        return _FakeAnthropicStream(response)


def _patch_anthropic_transport(monkeypatch, fake: _FakeAnthropicClient) -> dict:
    """替换 build_anthropic_client，返回记录其构造参数的字典。"""
    import agent.anthropic_adapter as adapter

    factory_calls: dict = {}

    def fake_factory(api_key, base_url, timeout=None, **kwargs):
        factory_calls.update({"api_key": api_key, "base_url": base_url, "timeout": timeout})
        return fake

    monkeypatch.setattr(adapter, "build_anthropic_client", fake_factory)
    return factory_calls


def test_chat_completions_path_follows_global_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HERMES_DEPLOYMENT_CONFIG", raising=False)
    _use_custom_qwen_global()

    from openai import AsyncOpenAI

    from server.nl2sql.algorithm.llm import LLMClient

    client = LLMClient()
    assert client.provider == "custom"
    assert client.model == "qwen-test"
    assert isinstance(client._client, AsyncOpenAI)

    completions = _record_client(client)
    text = asyncio.run(client.chat_completion("查一下基金数量", system_prompt="你是问数助手"))
    assert text == "SELECT 1"
    assert completions.kwargs["messages"] == [
        {"role": "system", "content": "你是问数助手"},
        {"role": "user", "content": "查一下基金数量"},
    ]
    assert completions.kwargs["model"] == "qwen-test"
    # 默认不传 max_tokens —— 由模型适配层按模型表兜底（见 deployment 段说明）
    assert "max_tokens" not in completions.kwargs


def test_max_output_tokens_passthrough(monkeypatch, tmp_path) -> None:
    _use_custom_qwen_global()
    _write_deployment_yaml(
        tmp_path,
        monkeypatch,
        """
nl2sql:
  max_output_tokens: 8192
""",
    )

    from server.nl2sql.algorithm.llm import LLMClient

    client = LLMClient()
    # 只配 max_output_tokens 时 provider/model 仍跟随全局主模型
    assert client.provider == "custom"
    assert client.model == "qwen-test"

    completions = _record_client(client)
    asyncio.run(client.chat_completion("hi"))
    assert completions.kwargs["max_tokens"] == 8192


def test_nl2sql_section_overrides_global_model_with_kimi_coding(monkeypatch, tmp_path) -> None:
    """deployment.yaml nl2sql 段把问数切到 kimi-coding，全局主模型保持 custom 不变。"""
    _use_custom_qwen_global()
    _write_deployment_yaml(
        tmp_path,
        monkeypatch,
        """
nl2sql:
  provider: kimi-coding
  model: k3
  max_output_tokens: 4096
""",
    )
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-testkey")

    fake = _FakeAnthropicClient(text="ok-text")
    factory_calls = _patch_anthropic_transport(monkeypatch, fake)

    from agent.auxiliary_client import AsyncAnthropicAuxiliaryClient
    from server.nl2sql.algorithm.llm import LLMClient

    client = LLMClient()
    assert client.provider == "kimi-coding"
    assert client.model == "k3"
    assert isinstance(client._client, AsyncAnthropicAuxiliaryClient)

    text = asyncio.run(client.chat_completion("各基金类别数量？", system_prompt="系统提示"))
    # 真实 build_anthropic_kwargs 翻译 + normalize_response 规范化后的文本回传
    assert text == "ok-text"

    # kimi-coding 自动路由：sk-kimi- key → api.kimi.com/coding
    assert factory_calls["api_key"] == "sk-kimi-testkey"
    assert "kimi" in factory_calls["base_url"]

    # 翻译成 Anthropic Messages 形状：system 顶层参数、max_tokens 透传
    assert fake.captured is not None
    assert fake.captured["system"] == "系统提示"
    assert fake.captured["messages"] == [{"role": "user", "content": "各基金类别数量？"}]
    assert fake.captured["max_tokens"] == 4096
    assert fake.captured["model"] == "k3"


def test_unknown_api_mode_raises_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HERMES_DEPLOYMENT_CONFIG", raising=False)
    _use_custom_qwen_global()

    import server.nl2sql.algorithm.llm as llm_mod
    from server.nl2sql.algorithm import Nl2sqlError

    monkeypatch.setattr(
        llm_mod,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "codex",
            "api_mode": "codex_responses",
            "base_url": "http://x.internal",
            "api_key": "k",
        },
    )

    with pytest.raises(Nl2sqlError, match="codex_responses"):
        llm_mod.LLMClient()
