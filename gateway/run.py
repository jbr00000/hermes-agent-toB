"""Compatibility stub for the removed messaging gateway runner.

The to-B fork no longer ships or starts chat-platform gateway adapters.  A few
legacy modules still import helper names from ``gateway.run`` during startup or
tests, so this module keeps the small import surface without retaining the old
runner implementation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway.run")

_AGENT_PENDING_SENTINEL = object()
_INTERRUPT_REASON_STOP = "stop"
_gateway_runner_ref = lambda: None


def _default_hermes_home() -> Path:
    try:
        from hermes_cli.config import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


_hermes_home = _default_hermes_home()


class GatewayDisabledError(RuntimeError):
    """Raised when removed gateway runtime behavior is requested directly."""


def _gateway_removed_message() -> str:
    return "Hermes gateway messaging platforms have been removed in this to-B fork."


def _load_gateway_config() -> dict:
    """Load the shared Hermes config for legacy callers.

    Kept for helper modules that previously read ``gateway`` settings through
    ``gateway.run``.  This does not enable the removed gateway runtime.
    """

    config_path = _hermes_home / "config.yaml"
    raw: dict[str, Any] = {}
    used_canonical = False
    try:
        from hermes_cli.config import get_config_path, read_raw_config

        if config_path == get_config_path():
            raw = read_raw_config()
            used_canonical = True
    except Exception:
        pass

    if not used_canonical:
        try:
            if config_path.exists():
                import yaml

                with config_path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
        except Exception:
            logger.debug("Could not load gateway config from %s", config_path, exc_info=True)
            raw = {}

    try:
        from hermes_cli import managed_scope

        raw = managed_scope.apply_managed_overlay(raw if isinstance(raw, dict) else {})
    except Exception:
        pass

    if not isinstance(raw, dict):
        return {}

    try:
        from hermes_cli.config import _normalize_root_model_keys

        raw = _normalize_root_model_keys(raw)
    except Exception:
        pass
    return raw


def _load_gateway_runtime_config() -> dict:
    cfg = _load_gateway_config()
    if not isinstance(cfg, dict) or not cfg:
        return {}
    try:
        from hermes_cli.config import _expand_env_vars

        expanded = _expand_env_vars(cfg)
        return expanded if isinstance(expanded, dict) else {}
    except Exception:
        return cfg


def _resolve_gateway_model(config: dict | None = None) -> str:
    cfg = config if config is not None else _load_gateway_config()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    if isinstance(model_cfg, str):
        return model_cfg
    if isinstance(model_cfg, dict):
        return str(model_cfg.get("default") or model_cfg.get("model") or "")
    return ""


def _resolve_runtime_agent_kwargs(config: dict | None = None) -> dict:
    return {}


def _platform_config_key(platform: Any) -> str:
    value = getattr(platform, "value", platform)
    return str(value or "").strip().lower().replace("-", "_")


def _home_target_env_var(platform: Any) -> str:
    key = _platform_config_key(platform).upper() or "GATEWAY"
    return f"{key}_HOME_TARGET"


def _home_thread_env_var(platform: Any) -> str:
    key = _platform_config_key(platform).upper() or "GATEWAY"
    return f"{key}_HOME_THREAD"


def _telegramize_command_mentions(text: str, bot_username: str | None = None) -> str:
    if not bot_username:
        return text
    return text.replace("@HermesBot", f"@{bot_username.lstrip('@')}")


def _resolve_hermes_bin() -> str:
    return shutil.which("hermes") or sys.executable


def _select_cached_agent_history(
    persisted_history: list[dict[str, Any]],
    live_history: Any,
) -> list[dict[str, Any]]:
    if isinstance(live_history, list) and len(live_history) > len(persisted_history):
        return list(live_history)
    return persisted_history


def _normalize_empty_agent_response(
    agent_result: dict,
    response: str,
    *,
    history_len: int = 0,
) -> str:
    if response:
        return response
    if agent_result.get("failed"):
        error_detail = agent_result.get("error", "unknown error")
        error_text = str(error_detail)
        lowered = error_text.lower()
        if any(part in lowered for part in ("context", "token", "too large", "too long", "exceed")):
            return "Session too large for the model context window. Use /compact or /reset."
        if "400" in lowered and history_len > 50:
            return "Session too large for the model context window. Use /compact or /reset."
        return f"The request failed: {error_text[:300]}\nTry again or use /reset to start fresh."
    api_calls = int(agent_result.get("api_calls", 0) or 0)
    if api_calls > 0 and not agent_result.get("interrupted"):
        if agent_result.get("partial"):
            return f"Processing stopped: {str(agent_result.get('error', 'processing incomplete'))[:200]}. Try again."
        return "Processing completed but no response was generated. Try sending your message again."
    return "No response was generated. Try sending your message again."


def _format_gateway_process_notification(evt: dict) -> str | None:
    if not isinstance(evt, dict):
        return None
    message = evt.get("message") or evt.get("text") or evt.get("output")
    if message:
        return str(message)
    event_type = evt.get("type")
    if event_type:
        return f"[{event_type}]"
    return None


def _drain_gateway_watch_events(completion_queue) -> list[dict]:
    watch_events: list[dict] = []
    requeue: list[dict] = []
    while True:
        try:
            evt = completion_queue.get_nowait()
        except (queue.Empty, AttributeError):
            break
        if not isinstance(evt, dict):
            continue
        evt_type = evt.get("type", "completion")
        if evt_type in {"watch_match", "watch_disabled"}:
            watch_events.append(evt)
        elif evt_type == "async_delegation":
            requeue.append(evt)
    for evt in requeue:
        completion_queue.put(evt)
    return watch_events


def _start_cron_ticker(stop_event: threading.Event, adapters=None, loop=None, interval: int = 60):
    """Return a dormant ticker thread placeholder for legacy callers."""

    def _noop() -> None:
        while not stop_event.wait(interval):
            logger.debug("gateway cron ticker disabled in to-B fork")
            break

    thread = threading.Thread(target=_noop, name="gateway-cron-disabled", daemon=True)
    thread.start()
    return thread


def run_sync(awaitable):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    if loop.is_running():
        raise GatewayDisabledError("run_sync cannot block an already-running event loop")
    return loop.run_until_complete(awaitable)


class GatewayRunner:
    """Minimal placeholder for the removed messaging gateway runtime."""

    disabled = True

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False

    async def start(self) -> bool:
        logger.warning(_gateway_removed_message())
        self.started = False
        return False

    async def stop(self) -> None:
        self.started = False

    async def run(self) -> bool:
        return await self.start()

    def __getattr__(self, name: str):
        raise GatewayDisabledError(f"gateway runtime method {name!r} is unavailable in this to-B fork")


async def start_gateway(config: Any = None, replace: bool = False, verbosity: int | None = 0) -> bool:
    logger.warning(_gateway_removed_message())
    return False


def main() -> int:
    print(_gateway_removed_message())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
