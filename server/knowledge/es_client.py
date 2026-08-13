"""Elasticsearch client for knowledge chunks (BM25 full-text projection).

Adapted from the reference project's ``infra/es.py``: a thin ``requests``
wrapper (no official elasticsearch dependency) with index creation, bulk
insert and term deletion. The client is built from ``deployment.yaml``'s
``knowledge.es_url``; the optional password comes from ``.env``
(``KNOWLEDGE_ES_PASSWORD``), never from config files.
"""
from __future__ import annotations

import json
import os
from threading import RLock
from typing import Any

import requests

from server.deployment_config import KnowledgeDeploymentConfig, load_deployment_config

from . import KnowledgeDisabledError

JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
NDJSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/x-ndjson"}

CHUNK_INDEX_NAME = "knowledge_chunks"


class ElasticsearchClient:
    """Small ES HTTP wrapper for index creation, bulk insert and deletion."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str = "",
        password: str = "",
        request_timeout: int = 30,
        bulk_timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.bulk_timeout = bulk_timeout
        self.session = requests.Session()
        if username and password:
            self.session.auth = (username, password)
        self.indices = _IndicesFacade(self)

    def create_index(
        self,
        *,
        index: str,
        mappings: dict[str, Any],
        settings: dict[str, Any] | None = None,
    ) -> None:
        if self.indices.exists(index=index):
            return
        body: dict[str, Any] = {"mappings": mappings}
        if settings:
            body["settings"] = settings
        self._request("PUT", index, json_body=body)

    def bulk_insert(
        self,
        index_name: str,
        documents: list[dict[str, Any]],
        *,
        id_field: str | None = None,
    ) -> dict[str, int]:
        if not documents:
            return {"success": 0, "failed": 0, "total": 0}

        lines: list[str] = []
        for document in documents:
            action: dict[str, Any] = {"_index": index_name}
            if id_field and document.get(id_field) is not None:
                action["_id"] = str(document[id_field])
            lines.append(json.dumps({"index": action}, ensure_ascii=False))
            lines.append(json.dumps(document, ensure_ascii=False))
        payload = ("\n".join(lines) + "\n").encode("utf-8")

        response = self._request(
            "POST",
            "_bulk",
            headers=NDJSON_HEADERS,
            params={"refresh": "false"},
            data=payload,
            timeout=self.bulk_timeout,
        )
        items = response.get("items", [])
        failed = sum(1 for item in items if _bulk_item_failed(item))
        return {"success": len(documents) - failed, "failed": failed, "total": len(documents)}

    def delete_by_term(self, index_name: str, field_name: str, field_value: Any) -> dict[str, Any]:
        """Delete documents by one exact field. Missing index is treated as empty."""
        if field_value is None:
            return {"deleted": 0}
        return self._request(
            "POST",
            f"{index_name}/_delete_by_query",
            params={"conflicts": "proceed", "refresh": "true", "ignore_unavailable": "true"},
            json_body={"query": {"term": {field_name: str(field_value)}}},
            timeout=self.bulk_timeout,
        )

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{index}/_search", json_body=body)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{str(path).lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: bytes | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method,
            self._url(path),
            headers=headers or JSON_HEADERS,
            params=params,
            json=json_body,
            data=data,
            timeout=timeout or self.request_timeout,
        )
        response.raise_for_status()
        return _response_json(response)


class _IndicesFacade:
    def __init__(self, owner: ElasticsearchClient):
        self._owner = owner

    def exists(self, *, index: str) -> bool:
        response = self._owner.session.request(
            "HEAD",
            self._owner._url(index),
            headers={"Accept": "application/json"},
            timeout=self._owner.request_timeout,
        )
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return False

    def delete(self, *, index: str) -> dict[str, Any]:
        return self._owner._request("DELETE", index)


def chunk_index_mappings() -> dict[str, Any]:
    """ES mapping for knowledge chunks: keyword ids + BM25 text fields."""
    return {
        "properties": {
            "id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "doc_pos": {"type": "integer"},
            "chunk_title": {"type": "text"},
            "chunk_content": {"type": "text"},
            "token_num": {"type": "integer"},
            "is_use": {"type": "keyword"},
        }
    }


def _response_json(response: Any) -> dict[str, Any]:
    if getattr(response, "content", None) == b"":
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _bulk_item_failed(item: dict[str, Any]) -> bool:
    result = item.get("index") or item.get("create") or item.get("update") or item.get("delete") or {}
    status = int(result.get("status") or 0)
    return status >= 300


_CLIENT_LOCK = RLock()
_CLIENT: ElasticsearchClient | None = None
_CLIENT_KEY: str | None = None


def get_es_client(config: KnowledgeDeploymentConfig | None = None) -> ElasticsearchClient:
    """Return the cached ES client for the current deployment config."""
    global _CLIENT, _CLIENT_KEY
    cfg = config or load_deployment_config().knowledge
    if not cfg.enabled:
        raise KnowledgeDisabledError("knowledge.enabled=false，知识库未启用")
    if not cfg.es_url:
        raise KnowledgeDisabledError("knowledge.es_url 未配置")
    password = os.environ.get("KNOWLEDGE_ES_PASSWORD", "")
    client_key = f"{cfg.es_url}|{bool(password)}"
    if _CLIENT is not None and _CLIENT_KEY == client_key:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != client_key:
            if _CLIENT is not None:
                _CLIENT.session.close()
            _CLIENT = ElasticsearchClient(
                base_url=cfg.es_url,
                username="elastic" if password else "",
                password=password,
            )
            _CLIENT_KEY = client_key
        return _CLIENT


def reset_es_client_for_tests() -> None:
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.session.close()
        _CLIENT = None
        _CLIENT_KEY = None
