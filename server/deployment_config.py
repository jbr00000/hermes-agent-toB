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
class KnowledgeEmbeddingConfig:
    """Customer-hosted OpenAI-compatible embedding endpoint (no outbound SaaS)."""

    base_url: str = ""
    model: str = ""
    dim: int = 1024
    api_key_env: str = "KNOWLEDGE_EMBEDDING_API_KEY"
    batch_size: int = 32


@dataclass(frozen=True)
class SemanticChunkingConfig:
    """语义分块参数（chunk_mode=semantic 时生效；默认值对齐 chonkie）。"""

    threshold: float = 0.8  # 极小值分位数阈值，越小切得越保守（块越大）
    similarity_window: int = 3  # 衡量"下一句"时回看几句
    min_sentences_per_chunk: int = 1  # 两个切点的最小句距
    min_characters_per_sentence: int = 24  # 短于此的句子并入前句
    filter_window: int = 5  # Savitzky-Golay 窗口（奇数且 > filter_polyorder）
    filter_polyorder: int = 3
    filter_tolerance: float = 0.2  # 一阶导判零容差，越大切点越多


@dataclass(frozen=True)
class KnowledgeDeploymentConfig:
    """Enterprise knowledge-base construction (parse → chunk → embed → ES/Milvus).

    Disabled by default: without a ``knowledge:`` section the routes 404 and no
    worker is started. MinerU and the embedding endpoint are customer-hosted
    services reached over the customer network; secrets stay in .env.
    """

    enabled: bool = False
    mineru_url: str = ""
    # api = MinerU FastAPI 封装（POST /file_parse）；vlm = vLLM 裸起的
    # MinerU2.5 VLM（OpenAI 兼容），经 mineru-vl-utils 两段式客户端驱动
    mineru_mode: str = "api"
    es_url: str = ""
    milvus_uri: str = ""
    embedding: KnowledgeEmbeddingConfig = field(default_factory=KnowledgeEmbeddingConfig)
    # structural = 结构+token 递归切（默认，无额外 embedding 消耗）；
    # semantic = 句级 embedding 相似度找话题切换点（分块时多一轮 embedding 调用）
    chunk_mode: str = "structural"
    semantic: SemanticChunkingConfig = field(default_factory=SemanticChunkingConfig)
    chunk_size: int = 400
    chunk_overlap: int = 64
    min_chunk_tokens: int = 50  # 短于此 token 数的尾块并入前一块（表格块除外）
    max_file_mb: int = 100


@dataclass(frozen=True)
class DataPermissionsConfig:
    """Table-level data permissions for db_query, keyed by role.

    ``roles`` maps a role name (e.g. ``"user"``) to the whitelist of table
    names that role may touch (lowercased at load; entries may carry a
    ``db.table`` qualifier). A role absent from the map is unrestricted.
    Disabled by default: without a ``data_permissions:`` section every role
    keeps full read access to the business DB.
    """

    enabled: bool = False
    roles: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DeploymentConfig:
    customer_id: str | None = None
    model: dict[str, Any] = field(default_factory=dict)
    database: DatabaseDeploymentConfig = field(default_factory=DatabaseDeploymentConfig)
    sandbox: SandboxDeploymentConfig = field(default_factory=SandboxDeploymentConfig)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=lambda: {"host_terminal": False})
    knowledge: KnowledgeDeploymentConfig = field(default_factory=KnowledgeDeploymentConfig)
    data_permissions: DataPermissionsConfig = field(default_factory=DataPermissionsConfig)


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


def _mineru_mode(value: Any) -> str:
    mode = str(value or "api").strip().lower()
    return mode if mode in ("api", "vlm") else "api"


def _chunk_mode(value: Any) -> str:
    mode = str(value or "structural").strip().lower()
    return mode if mode in ("structural", "semantic") else "structural"


def _data_permission_roles(value: Any) -> dict[str, list[str]]:
    """{role: [table, ...]}；表名统一小写，非字符串项丢弃。"""
    roles: dict[str, list[str]] = {}
    for role, tables in _as_dict(value).items():
        if not isinstance(tables, list):
            continue
        normalized = [str(table).strip().lower() for table in tables if str(table).strip()]
        roles[str(role).strip().lower()] = normalized
    return roles


def _unit_float(value: Any, default: float) -> float:
    """(0, 1) 开区间的浮点配置，越界回落默认。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0.0 < parsed < 1.0 else default


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
    knowledge = _as_dict(raw.get("knowledge"))
    data_permissions = _as_dict(raw.get("data_permissions"))
    embedding = _as_dict(knowledge.get("embedding"))
    semantic = _as_dict(knowledge.get("semantic"))
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
        knowledge=KnowledgeDeploymentConfig(
            enabled=bool(knowledge.get("enabled", False)),
            mineru_url=str(knowledge.get("mineru_url") or ""),
            mineru_mode=_mineru_mode(knowledge.get("mineru_mode")),
            es_url=str(knowledge.get("es_url") or ""),
            milvus_uri=str(knowledge.get("milvus_uri") or ""),
            embedding=KnowledgeEmbeddingConfig(
                base_url=str(embedding.get("base_url") or ""),
                model=str(embedding.get("model") or ""),
                dim=_positive_int(embedding.get("dim"), 1024),
                api_key_env=str(embedding.get("api_key_env") or "KNOWLEDGE_EMBEDDING_API_KEY"),
                batch_size=_positive_int(embedding.get("batch_size"), 32),
            ),
            chunk_mode=_chunk_mode(knowledge.get("chunk_mode")),
            semantic=SemanticChunkingConfig(
                threshold=_unit_float(semantic.get("threshold"), 0.8),
                similarity_window=_positive_int(semantic.get("similarity_window"), 3),
                min_sentences_per_chunk=_positive_int(
                    semantic.get("min_sentences_per_chunk"), 1
                ),
                min_characters_per_sentence=_positive_int(
                    semantic.get("min_characters_per_sentence"), 24
                ),
                filter_window=_positive_int(semantic.get("filter_window"), 5),
                filter_polyorder=_positive_int(semantic.get("filter_polyorder"), 3),
                filter_tolerance=_unit_float(semantic.get("filter_tolerance"), 0.2),
            ),
            chunk_size=_positive_int(knowledge.get("chunk_size"), 400),
            chunk_overlap=_positive_int(knowledge.get("chunk_overlap"), 64),
            min_chunk_tokens=_positive_int(knowledge.get("min_chunk_tokens"), 50),
            max_file_mb=_positive_int(knowledge.get("max_file_mb"), 100),
        ),
        data_permissions=DataPermissionsConfig(
            enabled=bool(data_permissions.get("enabled", False)),
            roles=_data_permission_roles(data_permissions.get("roles")),
        ),
    )
