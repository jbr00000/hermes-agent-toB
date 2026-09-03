"""问数 LLM 客户端 —— 复用 server RuntimeConfig 的 provider/model 解析。

对齐 lone-ai ``core/llm_functions.py`` 的调用契约：
``chat_completion(user_prompt, system_prompt=None, history_messages=None,
temperature=0) -> str``，但底层不再走 lone-ai 的 Config/client_factory，
而是本仓无头服务同一条链路：``load_runtime_config()`` →
``resolve_runtime_provider()`` → AsyncOpenAI（chat_completions）。

限制：kimi-coding 是 anthropic_messages 协议，问数链路不支持，会在构造时
报清晰错误（而不是发出一个必然 400 的请求）。
"""
from __future__ import annotations

import logging
from typing import Any

from hermes_cli.runtime_provider import resolve_runtime_provider
from server.runtime_config import load_runtime_config

from . import Nl2sqlError

logger = logging.getLogger(__name__)

_TOKEN_ENCODING = "cl100k_base"
_encoding_cache: Any = None


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


class LLMClient:
    """问数专用 LLM 客户端（无状态，可按需构造；构造只做配置解析不发请求）。"""

    def __init__(self) -> None:
        runtime = load_runtime_config()
        resolved = resolve_runtime_provider(requested=runtime.provider, target_model=runtime.model)
        if resolved.get("api_mode") != "chat_completions":
            raise Nl2sqlError(
                f"问数链路要求 OpenAI chat_completions 协议的模型服务，"
                f"当前 provider「{runtime.provider}」是 {resolved.get('api_mode')} "
                f"（kimi-coding 不支持问数，请在 config.yaml 换用 deepseek/zai/alibaba/custom）"
            )
        if not resolved.get("base_url") or not resolved.get("api_key"):
            raise Nl2sqlError(f"问数模型服务未配置完整（provider={runtime.provider} 缺 base_url/api_key）")
        self.provider = runtime.provider
        self.model = runtime.model

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=resolved["base_url"], api_key=resolved["api_key"])

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
