"""Feature flags — toggle elevated capabilities (computer_use, host terminal).

Both default OFF (least privilege): the agent runs in the Docker sandbox only
and has no desktop-control capability. The frontend will expose these as opt-in
buttons (with a warning) — a user flips one on only when they explicitly want
that power. This is the toggle layer for decision 5's "no host access by
default, opt-in elevated capabilities."

Read from config.yaml ``features:`` section, with ``HERMES_FEATURE_*`` env
override (env wins when set).
"""
from __future__ import annotations

import os

_DEFAULTS = {"computer_use": False, "host_terminal": False}


def get_features() -> dict:
    """Return the current feature flags (computer_use, host_terminal)."""
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
    # Env override (precedence when set).
    for key in _DEFAULTS:
        env_val = os.environ.get(f"HERMES_FEATURE_{key.upper()}")
        if env_val is not None and env_val.strip():
            flags[key] = env_val.strip().lower() in ("1", "true", "yes", "on")
    return flags


def apply_terminal_backend() -> None:
    """Force TERMINAL_ENV from the host_terminal flag, before the agent runs.

    host_terminal=True  -> 'local' (agent may run shell on the HOST/server).
    host_terminal=False -> 'docker' (sandbox only — the to-B default).
    """
    flags = get_features()
    os.environ["TERMINAL_ENV"] = "local" if flags.get("host_terminal") else "docker"
