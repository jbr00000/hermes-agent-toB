"""问数 LLM 客户端 —— 复用 server RuntimeConfig 的 provider/model 解析。

对齐 lone-ai ``core/llm_functions.py`` 的调用契约：
``chat_completion(user_prompt, system_prompt=None, history_messages=None,
temperature=0) -> str``，但底层不再走 lone-ai 的 Config/client_factory，
而是本仓无头服务同一条链路：``load_runtime_config()`` →
``resolve_runtime_provider()``。

模型选择：deployment.yaml 的 ``nl2sql:`` 段可给问数单独配模型
（provider/model/base_url/api_key_env/max_output_tokens），逐项缺省回落
到全局主模型（config.yaml model 段）。

协议支持（按 resolve_runtime_provider 的 api_mode 分派）：
  - chat_completions   → AsyncOpenAI（deepseek/zai/alibaba/custom/moonshot 旧 key）
  - anthropic_messages → 复用主链路适配层（anthropic_adapter.build_anthropic_client
                         + auxiliary_client 的 Anthropic→OpenAI 包装），
                         kimi-coding（api.kimi.com/coding）走这条路；
                         适配层已内置 kimi 必需的 User-Agent/beta 头处理
其余 api_mode（codex_responses 等）报清晰错误。
"""
from __future__ import annotations

import logging
import os
from typing import Any

from hermes_cli.runtime_provider import resolve_runtime_provider
from server.deployment_config import load_deployment_config
from server.runtime_config import load_runtime_config

from . import Nl2sqlError

logger = logging.getLogger(__name__)

_TOKEN_ENCODING = "cl100k_base"
_encoding_cache: Any = None

# 构造 anthropic 客户端时的读超时（秒）；chat_completions 路径仍按调用方
# timeout 逐请求传递（适配层会忽略未知 kwargs，不冲突）
_ANTHROPIC_CLIENT_TIMEOUT = 120.0


def count_tokens(text: str) -> int:
    """tiktoken cl100k_base 计数；本地缺编码数据时退化为粗估，不让计数拖垮主链路。"""
    global _encoding_cache
    if not text:
        return 0
    if _encoding_cache is None:
        try:
            import tiktoken

            _encoding_cache = tiktoken.get_encoding(_TOKEN_ENCODING)
        except Exception as exc:  # pragma: no cover - 依赖环境问题
            logger.warning("tiktoken 编码加载失败，token 计数退化为粗估: %s", exc)
            _encoding_cache = False
    if _encoding_cache is False:
        return max(1, len(text) // 4)
    return len(_encoding_cache.encode(text))


def _build_anthropic_chat_client(api_key: str, base_url: str, model: str) -> Any:
    """anthropic_messages 协议 → 包装出 OpenAI 形状的 async chat 客户端。

    复用主 chat 链路的同一套适配层：build_anthropic_client 处理 kimi-coding
    的 User-Agent/beta 头等端点细节；AnthropicAuxiliaryClient 把
    messages.create 翻译成 Anthropic Messages 并把响应规范化回 OpenAI 形状。
    """
    from agent.anthropic_adapter import build_anthropic_client
    from agent.auxiliary_client import AnthropicAuxiliaryClient, AsyncAnthropicAuxiliaryClient

    real_client = build_anthropic_client(api_key, base_url, timeout=_ANTHROPIC_CLIENT_TIMEOUT)
    sync_wrapper = AnthropicAuxiliaryClient(real_client, model, api_key, base_url, is_oauth=False)
    return AsyncAnthropicAuxiliaryClient(sync_wrapper)


class LLMClient:
    """问数专用 LLM 客户端（无状态，可按需构造；构造只做配置解析不发请求）。"""

    def __init__(self) -> None:
        runtime = load_runtime_config()
        dep = load_deployment_config().nl2sql
        self.provider = dep.provider or runtime.provider
        self.model = dep.model or runtime.model
        self._max_output_tokens = dep.max_output_tokens

        explicit_api_key = ""
        if dep.api_key_env:
            explicit_api_key = os.environ.get(dep.api_key_env, "").strip()
        resolved = resolve_runtime_provider(
            requested=self.provider,
            target_model=self.model,
            explicit_base_url=dep.base_url or None,
            explicit_api_key=explicit_api_key or None,
        )
        if not resolved.get("base_url") or not resolved.get("api_key"):
            raise Nl2sqlError(f"问数模型服务未配置完整（provider={self.provider} 缺 base_url/api_key）")

        api_mode = resolved.get("api_mode")
        if api_mode == "chat_completions":
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=resolved["base_url"], api_key=resolved["api_key"])
        elif api_mode == "anthropic_messages":
            self._client = _build_anthropic_chat_client(
                resolved["api_key"], resolved["base_url"], self.model
            )
        else:
            raise Nl2sqlError(
                f"问数链路要求 chat_completions 或 anthropic_messages 协议的模型服务，"
                f"当前 provider「{self.provider}」是 {api_mode} "
                f"（请在 config.yaml 或 deployment.yaml 的 nl2sql 段换用 "
                f"deepseek/zai/alibaba/custom/kimi-coding）"
            )

    async def chat_completion(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        temperature: float = 0.0,
        timeout: float | None = 120.0,
    ) -> str:
        """单次对话补全 → 文本。失败抛 Nl2sqlError（编排器据此走重试/报错分支）。"""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": user_prompt})

        # enable_thinking=False 是 zai(GLM) 私有参数，只对它发，避免 deepseek 等拒绝未知字段
        # 走 **kwargs（本仓 auxiliary_client 同惯例），TypedDict 参数约束交给运行时
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout,
        }
        if self._max_output_tokens > 0:
            kwargs["max_tokens"] = self._max_output_tokens
        if self.provider == "zai":
            kwargs["extra_body"] = {"enable_thinking": False}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise Nl2sqlError(f"大模型调用失败: {exc}") from exc
        choice = response.choices[0] if response.choices else None
        content = (choice.message.content or "") if choice and choice.message else ""
        if not content.strip():
            raise Nl2sqlError("大模型返回为空")
        return content
