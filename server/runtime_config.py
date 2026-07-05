"""Runtime configuration for the headless server agent path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_constants import parse_reasoning_effort
from hermes_cli.config import load_config


DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_REASONING_CONFIG = {"enabled": False}
_SUPPORTED_PROVIDERS = frozenset({"deepseek", "zai", "alibaba", "custom"})


@dataclass(frozen=True)
class RuntimeConfig:
    provider: str
    model: str
    reasoning_config: dict[str, Any] | None


def _infer_provider(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("deepseek"):
        return "deepseek"
    if normalized.startswith(("glm", "zai")):
        return "zai"
    if normalized.startswith(("qwen", "dashscope", "bailian")):
        return "alibaba"
    return DEFAULT_PROVIDER


def _normalize_provider(provider: object, model: str) -> str:
    candidate = str(provider or "").strip().lower()
    if not candidate:
        candidate = _infer_provider(model)
    if candidate not in _SUPPORTED_PROVIDERS:
        return DEFAULT_PROVIDER
    return candidate


def _resolve_model_config(config: dict[str, Any]) -> tuple[str, object]:
    raw = config.get("model")
    if isinstance(raw, str):
        model = raw.strip()
        return (model or DEFAULT_MODEL), None
    if isinstance(raw, dict):
        model = str(raw.get("default") or raw.get("model") or "").strip()
        return (model or DEFAULT_MODEL), raw.get("provider")
    return DEFAULT_MODEL, None


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
    model, explicit_provider = _resolve_model_config(config)
    provider = _normalize_provider(explicit_provider, model)
    return RuntimeConfig(
        provider=provider,
        model=model,
        reasoning_config=_resolve_reasoning_config(config),
    )
