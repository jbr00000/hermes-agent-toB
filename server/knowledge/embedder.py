"""Embedding client for the knowledge pipeline.

Calls the customer-hosted OpenAI-compatible embedding endpoint (e.g. bge-m3
served by Xinference/vLLM on the customer network — nothing leaves the
deployment). Endpoint/model come from ``deployment.yaml``; the API key comes
from ``.env`` via ``embedding.api_key_env`` (default
``KNOWLEDGE_EMBEDDING_API_KEY``).
"""
from __future__ import annotations

import os
from threading import RLock

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config

from . import KnowledgeDisabledError, KnowledgeError


class EmbedderError(KnowledgeError):
    """Embedding endpoint call failed or returned a malformed payload."""


class Embedder:
    """Batched OpenAI-compatible embeddings with dim validation."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        batch_size: int = 32,
        dim: int = 1024,
    ):
        if not base_url or not model:
            raise KnowledgeDisabledError("knowledge.embedding 的 base_url/model 未配置")
        self.model = model
        self.batch_size = max(1, batch_size)
        self.dim = dim
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-configured")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, preserving order. Raises EmbedderError on mismatch."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                response = self._client.embeddings.create(model=self.model, input=batch)
            except Exception as exc:
                raise EmbedderError(f"embedding 端点调用失败: {exc}") from exc
            items = sorted(response.data, key=lambda item: item.index)
            batch_vectors = [list(item.embedding) for item in items]
            if len(batch_vectors) != len(batch):
                raise EmbedderError(
                    f"embedding 返回数量与输入不一致: 输入 {len(batch)}，返回 {len(batch_vectors)}"
                )
            vectors.extend(batch_vectors)
        actual_dim = len(vectors[0]) if vectors else 0
        if vectors and self.dim and actual_dim != self.dim:
            raise EmbedderError(
                f"embedding 维度与配置不符: 配置 dim={self.dim}，实际 {actual_dim}。"
                "请修正 deployment.yaml 的 knowledge.embedding.dim"
            )
        return vectors


_CLIENT_LOCK = RLock()
_CLIENT: Embedder | None = None
_CLIENT_KEY: tuple[str, str, int] | None = None


def get_embedder(config: KnowledgeDeploymentConfig | None = None) -> Embedder:
    """Return the cached Embedder for the current deployment config."""
    global _CLIENT, _CLIENT_KEY
    cfg = config or load_deployment_config().knowledge
    if not cfg.enabled:
        raise KnowledgeDisabledError("knowledge.enabled=false，知识库未启用")
    api_key = os.environ.get(cfg.embedding.api_key_env, "")
    client_key = (cfg.embedding.base_url, cfg.embedding.model, cfg.embedding.dim)
    if _CLIENT is not None and _CLIENT_KEY == client_key:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != client_key:
            _CLIENT = Embedder(
                base_url=cfg.embedding.base_url,
                model=cfg.embedding.model,
                api_key=api_key,
                batch_size=cfg.embedding.batch_size,
                dim=cfg.embedding.dim,
            )
            _CLIENT_KEY = client_key
        return _CLIENT


def reset_embedder_for_tests() -> None:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        _CLIENT = None
        _CLIENT_KEY = None
