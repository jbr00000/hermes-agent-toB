"""Auxiliary (lightweight) LLM client for the knowledge pipeline.

主模型只生成最终回答；检索前后的"小任务"（查询改写/扩展/拆解/证据评估等，
精准模式的确定性流水线步骤）全部走这个轻量模型——短输入短输出、无状态
单次调用，不占主模型上下文、不影响 prompt 缓存。

OpenAI 兼容端点（如 Xinference 托管的 qwen-27B），配置来自
``deployment.yaml`` 的 ``knowledge.aux_llm``，密钥从 ``.env`` 读
（``aux_llm.api_key_env``，默认 ``KNOWLEDGE_AUX_LLM_API_KEY``）。
未配置（base_url 为空）时精准模式自动降级为快速模式——属正常路径，不报错。
"""
from __future__ import annotations

import os
from threading import RLock

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config

from . import KnowledgeError


class AuxLlmError(KnowledgeError):
    """Aux LLM endpoint call failed or returned a malformed payload."""


class AuxLlm:
    """Minimal OpenAI-compatible chat client for one-shot auxiliary tasks."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
    ):
        if not base_url or not model:
            raise KnowledgeError("knowledge.aux_llm 的 base_url/model 未配置")
        self.model = model
        from openai import OpenAI

        # 与 embedder 相同：客户内网地址，必须绕过本机系统代理
        import httpx

        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-configured",
            timeout=max(1.0, float(timeout_seconds)),
            http_client=httpx.Client(trust_env=False),
        )

    def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """One-shot chat completion; raises AuxLlmError on any failure."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
        except Exception as exc:
            raise AuxLlmError(f"辅助模型调用失败: {exc}") from exc
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise AuxLlmError("辅助模型返回结构异常") from exc
        return str(content or "").strip()


_CLIENT_LOCK = RLock()
_CLIENT: AuxLlm | None = None
_CLIENT_KEY: tuple[str, str] | None = None


def aux_llm_configured(config: KnowledgeDeploymentConfig | None = None) -> bool:
    """是否配置了辅助模型（未配置时精准模式降级快速，属正常路径）。"""
    cfg = config or load_deployment_config().knowledge
    return bool(cfg.enabled and cfg.aux_llm.base_url and cfg.aux_llm.model)


def get_aux_llm(config: KnowledgeDeploymentConfig | None = None) -> AuxLlm:
    """Return the cached AuxLlm for the current deployment config."""
    global _CLIENT, _CLIENT_KEY
    cfg = config or load_deployment_config().knowledge
    api_key = os.environ.get(cfg.aux_llm.api_key_env, "")
    client_key = (cfg.aux_llm.base_url, cfg.aux_llm.model)
    if _CLIENT is not None and _CLIENT_KEY == client_key:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != client_key:
            _CLIENT = AuxLlm(
                base_url=cfg.aux_llm.base_url,
                model=cfg.aux_llm.model,
                api_key=api_key,
                timeout_seconds=cfg.aux_llm.timeout_seconds,
            )
            _CLIENT_KEY = client_key
        return _CLIENT


def reset_aux_llm_for_tests() -> None:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        _CLIENT = None
        _CLIENT_KEY = None
