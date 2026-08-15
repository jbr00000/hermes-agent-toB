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


def test_knowledge_config_defaults_disabled(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DEPLOYMENT_CONFIG", raising=False)

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.knowledge.enabled is False
    assert config.knowledge.embedding.api_key_env == "KNOWLEDGE_EMBEDDING_API_KEY"
    assert config.knowledge.chunk_size > 0


def test_knowledge_config_from_yaml(monkeypatch, tmp_path) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(
        """
knowledge:
  enabled: true
  mineru_url: http://gpu-server:18888
  es_url: http://elasticsearch:19200
  milvus_uri: http://milvus:19530
  embedding:
    base_url: http://llm-gw.internal/v1
    model: bge-m3
    dim: 1024
    batch_size: 16
  chunk_size: 512
  min_chunk_tokens: 80
  max_file_mb: 50
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DEPLOYMENT_CONFIG", str(path))

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.knowledge.enabled is True
    assert config.knowledge.mineru_url == "http://gpu-server:18888"
    assert config.knowledge.es_url == "http://elasticsearch:19200"
    assert config.knowledge.embedding.model == "bge-m3"
    assert config.knowledge.embedding.batch_size == 16
    assert config.knowledge.chunk_size == 512
    assert config.knowledge.chunk_overlap == 64  # 未配置走默认
    assert config.knowledge.min_chunk_tokens == 80
    assert config.knowledge.max_file_mb == 50


def test_data_permissions_defaults_disabled(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DEPLOYMENT_CONFIG", raising=False)

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.data_permissions.enabled is False
    assert config.data_permissions.roles == {}


def test_data_permissions_from_yaml_normalizes_case(monkeypatch, tmp_path) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(
        """
data_permissions:
  enabled: true
  roles:
    User:
      - Orders
      - sales.Customers
    admin: not-a-list
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DEPLOYMENT_CONFIG", str(path))

    from server.deployment_config import load_deployment_config

    config = load_deployment_config()

    assert config.data_permissions.enabled is True
    assert config.data_permissions.roles["user"] == ["orders", "sales.customers"]
    # 非列表值整条丢弃（角色缺席 = 不限制，与文档约定一致）。
    assert "admin" not in config.data_permissions.roles
