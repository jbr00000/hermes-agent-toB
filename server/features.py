"""Feature flags for elevated headless-server capabilities.

The normal production path is Docker sandbox execution.

Host terminal is intentionally stricter than a normal feature flag. A to-B
deployment must not be able to flip the agent into host shell execution from
deployment.yaml/config.yaml/env alone; that would grant the agent host server
privileges. It requires an explicit break-glass process env,
``HERMES_ALLOW_HOST_TERMINAL=1``, and otherwise resolves to false.

``browser_automation`` (P3 兜底, docs/联网检索接入方案.md §8) enables the
browser_* toolset for full-permission agent tasks. It does NOT need the
break-glass env: unlike host_terminal it grants no host privilege — the
browser runs in a dedicated container, and intranet targets are bounded by
the ``browser.allowed_targets`` whitelist in config.yaml.
"""
from __future__ import annotations

import os

_DEFAULTS = {"host_terminal": False, "browser_automation": False}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _host_terminal_break_glass_enabled() -> bool:
    """Return true only when an operator explicitly permits host shell access."""
    return _truthy(os.environ.get("HERMES_ALLOW_HOST_TERMINAL"))


def get_features() -> dict:
    """Return the current feature flags."""
    flags = dict(_DEFAULTS)
    try:
        from server.deployment_config import load_deployment_config

        deployment_features = load_deployment_config().features
        for key in _DEFAULTS:
            if key in deployment_features:
                flags[key] = bool(deployment_features[key])
    except Exception:
        pass
    try:
        from hermes_cli.config import load_config

        cfg = load_config().get("features") or {}
        for key in _DEFAULTS:
            if key in cfg:
                flags[key] = bool(cfg[key])
    except Exception:
        pass

    for key in _DEFAULTS:
        env_val = os.environ.get(f"HERMES_FEATURE_{key.upper()}")
        if env_val is not None and env_val.strip():
            flags[key] = _truthy(env_val)

    if flags.get("host_terminal") and not _host_terminal_break_glass_enabled():
        flags["host_terminal"] = False
    return flags


def apply_terminal_backend() -> None:
    """Force TERMINAL_ENV from the resolved host_terminal flag."""
    flags = get_features()
    os.environ["HERMES_TOB_SERVER"] = "1"
    os.environ["TERMINAL_ENV"] = "local" if flags.get("host_terminal") else "docker"
