"""Headless-server adapter for deployment.yaml MCP server declarations."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _truthy_config(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def deployment_mcp_servers(config=None) -> dict[str, dict[str, Any]]:
    """Return MCP config as the mapping expected by tools.mcp_tool."""
    if config is None:
        from server.deployment_config import load_deployment_config

        config = load_deployment_config()

    servers: dict[str, dict[str, Any]] = {}
    for item in getattr(config, "mcp_servers", []) or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        servers[name] = {str(k): v for k, v in item.items() if k != "name"}
    return servers


def enabled_mcp_toolsets(config=None) -> list[str]:
    """Return toolset names for enabled deployment MCP servers."""
    toolsets: list[str] = []
    for name, server_cfg in deployment_mcp_servers(config).items():
        if _truthy_config(server_cfg.get("enabled", True), default=True):
            toolsets.append(f"mcp-{name}")
    return toolsets


def register_deployment_mcp_servers(config=None) -> list[str]:
    """Connect enabled deployment MCP servers and register their tools."""
    servers = deployment_mcp_servers(config)
    if not servers:
        return []
    try:
        from tools.mcp_tool import register_mcp_servers

        return register_mcp_servers(servers)
    except Exception:
        # MCP is optional and connection failures are already handled inside
        # tools.mcp_tool when the SDK is present. Keep agent construction alive.
        return []
