"""Declarative deployment configuration for customer installs.

This is intentionally small: it gives each customer deployment a single YAML
shape without replacing the existing config.yaml/.env mechanisms yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml

from hermes_constants import get_hermes_home


@dataclass(frozen=True)
class DatabaseDeploymentConfig:
    url_env: str = "HERMES_DB_URL"
    max_rows: int = 200
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SandboxDeploymentConfig:
    backend: str = "docker"
    network_egress: str = "deny"
    allowed_hosts: list[str] = field(default_factory=list)
    cpu_limit: str | None = None
    memory_limit: str | None = None
    pids_limit: int | None = None
    timeout_seconds: float = 300.0


@dataclass(frozen=True)
class DeploymentConfig:
    customer_id: str | None = None
    model: dict[str, Any] = field(default_factory=dict)
    database: DatabaseDeploymentConfig = field(default_factory=DatabaseDeploymentConfig)
    sandbox: SandboxDeploymentConfig = field(default_factory=SandboxDeploymentConfig)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=lambda: {"host_terminal": False})


def _config_path() -> Path:
    configured = os.environ.get("HERMES_DEPLOYMENT_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return get_hermes_home() / "deployment.yaml"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_deployment_config(path: str | os.PathLike[str] | None = None) -> DeploymentConfig:
    """Load deployment.yaml, returning secure defaults when it is absent."""
    cfg_path = Path(path) if path is not None else _config_path()
    if not cfg_path.exists():
        return DeploymentConfig()

    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raw = {}

    database = _as_dict(raw.get("database"))
    sandbox = _as_dict(raw.get("sandbox"))
    features = {"host_terminal": False}
    for key, value in _as_dict(raw.get("features")).items():
        if key in features:
            features[key] = bool(value)

    return DeploymentConfig(
        customer_id=str(raw["customer_id"]) if raw.get("customer_id") else None,
        model=_as_dict(raw.get("model")),
        database=DatabaseDeploymentConfig(
            url_env=str(database.get("url_env") or "HERMES_DB_URL"),
            max_rows=_positive_int(database.get("max_rows"), 200),
            timeout_seconds=_positive_float(database.get("timeout_seconds"), 30.0),
        ),
        sandbox=SandboxDeploymentConfig(
            backend=str(sandbox.get("backend") or "docker"),
            network_egress=str(sandbox.get("network_egress") or "deny"),
            allowed_hosts=[str(host) for host in (sandbox.get("allowed_hosts") or [])],
            cpu_limit=str(sandbox["cpu_limit"]) if sandbox.get("cpu_limit") else None,
            memory_limit=str(sandbox["memory_limit"]) if sandbox.get("memory_limit") else None,
            pids_limit=(
                _positive_int(sandbox.get("pids_limit"), 0)
                if sandbox.get("pids_limit") is not None
                else None
            ),
            timeout_seconds=_positive_float(sandbox.get("timeout_seconds"), 300.0),
        ),
        mcp_servers=_as_list_of_dicts(raw.get("mcp_servers")),
        features=features,
    )
