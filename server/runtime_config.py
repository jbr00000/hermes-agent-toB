"""Runtime configuration for the headless server agent path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_constants import parse_reasoning_effort
from hermes_cli.config import load_config


DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_REASONING_CONFIG = {"enabled": False}
# 模型上下文上限（附件注入的 token 预算基准）；config.yaml 的 model 段可用
# max_input_tokens 覆盖。默认 128K，对齐 deepseek/zai/alibaba 的主力模型。
DEFAULT_MAX_INPUT_TOKENS = 128_000
# kimi-coding：核心层（hermes_cli/auth.py + agent/anthropic_adapter.py）内置的
# Kimi Coding Plan（api.kimi.com/coding，Anthropic Messages 协议）；无头层只需
# 放行 provider 名，base_url/key 由核心层 resolve_provider_client 解析。
_SUPPORTED_PROVIDERS = frozenset({"deepseek", "zai", "alibaba", "custom", "kimi-coding"})


@dataclass(frozen=True)
class RuntimeConfig:
    provider: str
    model: str
    reasoning_config: dict[str, Any] | None
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS


def _infer_provider(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("deepseek"):
        return "deepseek"
    if normalized.startswith(("glm", "zai")):
        return "zai"
    if normalized.startswith(("qwen", "dashscope", "bailian")):
        return "alibaba"
    if normalized.startswith(("kimi", "moonshot", "k2", "k3")):
        return "kimi-coding"
    return DEFAULT_PROVIDER


def _normalize_provider(provider: object, model: str) -> str:
    candidate = str(provider or "").strip().lower()
    if not candidate:
        candidate = _infer_provider(model)
    if candidate not in _SUPPORTED_PROVIDERS:
        return DEFAULT_PROVIDER
    return candidate


def _resolve_model_config(config: dict[str, Any]) -> tuple[str, object, int]:
    raw = config.get("model")
    if isinstance(raw, str):
        model = raw.strip()
        return (model or DEFAULT_MODEL), None, DEFAULT_MAX_INPUT_TOKENS
    if isinstance(raw, dict):
        model = str(raw.get("default") or raw.get("model") or "").strip()
        max_input = raw.get("max_input_tokens")
        try:
            max_input_tokens = int(max_input) if max_input is not None else DEFAULT_MAX_INPUT_TOKENS
        except (TypeError, ValueError):
            max_input_tokens = DEFAULT_MAX_INPUT_TOKENS
        if max_input_tokens <= 0:
            max_input_tokens = DEFAULT_MAX_INPUT_TOKENS
        return (model or DEFAULT_MODEL), raw.get("provider"), max_input_tokens
    return DEFAULT_MODEL, None, DEFAULT_MAX_INPUT_TOKENS


def _resolve_reasoning_config(config: dict[str, Any]) -> dict[str, Any] | None:
    agent_cfg = config.get("agent")
    effort = None
    if isinstance(agent_cfg, dict):
        effort = agent_cfg.get("reasoning_effort")
    parsed = parse_reasoning_effort(effort)
    if parsed is not None:
        return parsed
    return dict(DEFAULT_REASONING_CONFIG)


def load_runtime_config() -> RuntimeConfig:
    config = load_config()
    model, explicit_provider, max_input_tokens = _resolve_model_config(config)
    provider = _normalize_provider(explicit_provider, model)
    return RuntimeConfig(
        provider=provider,
        model=model,
        reasoning_config=_resolve_reasoning_config(config),
        max_input_tokens=max_input_tokens,
    )
