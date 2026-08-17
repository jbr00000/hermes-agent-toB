"""Rerank client for the knowledge retriever (精准排序层).

协议移植自 lone-ai ``core/embedding_rerank.py`` 的 RerankClient：Jina 风格
``POST {base_url}/rerank``，请求 ``{model, query, documents, top_n}``，响应
``results: [{index, relevance_score}]``（bge-reranker-v2-m3 在 Xinference 上
即此协议）。与 embedder 一样走客户内网，httpx 必须 ``trust_env=False``
（Windows 系统代理会把 192.168.* 内网请求拐进本机代理导致 502）。

配置可选：``knowledge.rerank.base_url`` 为空时检索层直接跳过精排，不报错。
"""
from __future__ import annotations

import os
from threading import RLock

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config

from . import KnowledgeError


class RerankError(KnowledgeError):
    """Rerank endpoint call failed or returned a malformed payload."""


class Reranker:
    """Single-shot Jina-style rerank over a candidate list."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
    ):
        if not base_url or not model:
            raise KnowledgeError("knowledge.rerank 的 base_url/model 未配置")
        self.model = model
        self._url = f"{base_url.rstrip('/')}/rerank"
        self._timeout = max(1.0, float(timeout_seconds))
        import httpx

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            trust_env=False, timeout=self._timeout, headers=headers
        )

    def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[float]:
        """Return a relevance score per document (aligned with input order)."""
        if not documents:
            return []
        body: dict = {"model": self.model, "query": query, "documents": documents}
        if top_n and top_n > 0:
            body["top_n"] = min(top_n, len(documents))
        try:
            response = self._client.post(self._url, json=body)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RerankError(f"rerank 端点调用失败: {exc}") from exc
        results = payload.get("results")
        if not isinstance(results, list):
            raise RerankError("rerank 返回结构异常：缺少 results 列表")
        scores = [0.0] * len(documents)
        try:
            for item in results:
                scores[int(item["index"])] = float(item["relevance_score"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RerankError("rerank 返回结构异常：results 项缺少 index/relevance_score") from exc
        return scores


_CLIENT_LOCK = RLock()
_CLIENT: Reranker | None = None
_CLIENT_KEY: tuple[str, str] | None = None


def rerank_configured(config: KnowledgeDeploymentConfig | None = None) -> bool:
    """是否配置了 rerank 端点（未配置则检索层跳过精排，属正常路径）。"""
    cfg = config or load_deployment_config().knowledge
    return bool(cfg.enabled and cfg.rerank.base_url and cfg.rerank.model)


def get_reranker(config: KnowledgeDeploymentConfig | None = None) -> Reranker:
    """Return the cached Reranker for the current deployment config."""
    global _CLIENT, _CLIENT_KEY
    cfg = config or load_deployment_config().knowledge
    api_key = os.environ.get(cfg.rerank.api_key_env, "")
    client_key = (cfg.rerank.base_url, cfg.rerank.model)
    if _CLIENT is not None and _CLIENT_KEY == client_key:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != client_key:
            _CLIENT = Reranker(
                base_url=cfg.rerank.base_url,
                model=cfg.rerank.model,
                api_key=api_key,
                timeout_seconds=cfg.rerank.timeout_seconds,
            )
            _CLIENT_KEY = client_key
        return _CLIENT


def reset_reranker_for_tests() -> None:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        _CLIENT = None
        _CLIENT_KEY = None
