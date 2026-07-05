from __future__ import annotations


def test_deployment_mcp_servers_maps_list_shape(tmp_path) -> None:
    from server.deployment_config import load_deployment_config
    from server.mcp import deployment_mcp_servers, enabled_mcp_toolsets

    cfg_path = tmp_path / "deployment.yaml"
    cfg_path.write_text(
        """
mcp_servers:
  - name: metrics
    url: http://metrics.example/sse
    enabled: true
  - name: draft
    command: python
    args: ["server.py"]
    enabled: false
""",
        encoding="utf-8",
    )

    config = load_deployment_config(cfg_path)
    servers = deployment_mcp_servers(config)

    assert servers == {
        "metrics": {"url": "http://metrics.example/sse", "enabled": True},
        "draft": {"command": "python", "args": ["server.py"], "enabled": False},
    }
    assert enabled_mcp_toolsets(config) == ["mcp-metrics"]
