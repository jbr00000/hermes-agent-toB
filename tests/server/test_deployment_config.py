from __future__ import annotations

from pathlib import Path

import yaml


def test_load_deployment_config_defaults_are_secure(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DEPLOYMENT_CONFIG", raising=False)

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.customer_id is None
    assert config.sandbox.backend == "docker"
    assert config.sandbox.network_egress == "deny"
    assert config.features["host_terminal"] is False
    assert config.features["computer_use"] is False
    assert config.database.max_rows == 200


def test_load_deployment_config_from_yaml(monkeypatch, tmp_path) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(
        """
customer_id: acme
model:
  provider: deepseek
  default: deepseek-v4-pro
database:
  url_env: HERMES_DB_URL
  max_rows: 50
sandbox:
  backend: docker
  network_egress: allowlist
  allowed_hosts:
    - db.internal
mcp_servers:
  - name: metrics
    url: http://metrics-mcp.internal/sse
features:
  host_terminal: false
  computer_use: false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DEPLOYMENT_CONFIG", str(path))

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.customer_id == "acme"
    assert config.model == {"provider": "deepseek", "default": "deepseek-v4-pro"}
    assert config.database.url_env == "HERMES_DB_URL"
    assert config.database.max_rows == 50
    assert config.sandbox.network_egress == "allowlist"
    assert config.sandbox.allowed_hosts == ["db.internal"]
    assert config.mcp_servers == [{"name": "metrics", "url": "http://metrics-mcp.internal/sse"}]


def test_deployment_yaml_example_is_parseable() -> None:
    example = Path(__file__).resolve().parents[2] / "deployment.yaml.example"

    data = yaml.safe_load(example.read_text(encoding="utf-8"))

    assert data["sandbox"]["backend"] == "docker"
    assert data["database"]["url_env"] == "HERMES_DB_URL"
    assert data["features"]["host_terminal"] is False
