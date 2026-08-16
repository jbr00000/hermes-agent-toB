"""knowledge_search tool tests — retriever/repository/config 全部 fake。"""
from __future__ import annotations

import json

import pytest

import tools.knowledge_search as tool
from server.deployment_config import (
    KnowledgeDeploymentConfig,
    KnowledgeEmbeddingConfig,
    KnowledgeRetrievalConfig,
)
from server.knowledge import retriever


def _config(enabled=True, **overrides) -> KnowledgeDeploymentConfig:
    values = {
        "enabled": enabled,
        "es_url": "http://elasticsearch:19200",
        "milvus_uri": "http://milvus:19530",
        "embedding": KnowledgeEmbeddingConfig(
            base_url="http://llm-gw.internal/v1", model="bge-m3", dim=2
        ),
        "retrieval": KnowledgeRetrievalConfig(topk=6),
    }
    values.update(overrides)
    return KnowledgeDeploymentConfig(**values)


def _chunk(chunk_id, num_content=None):
    return {
        "chunk_id": chunk_id,
        "doc_id": f"doc-{chunk_id}",
        "doc_name": f"手册{chunk_id}.pdf",
        "kb_id": "kb-1",
        "chunk_title": f"章节{chunk_id}",
        "content": num_content if num_content is not None else f"正文 {chunk_id}",
        "doc_pos": 0,
        "score": 0.5,
    }


def _enable(monkeypatch, *, search_result=None, config=None) -> dict:
    """Wire fakes; returns a dict for capturing search_chunks kwargs."""
    captured: dict = {}
    monkeypatch.setattr(tool, "_knowledge_config", lambda: config or _config())

    def _fake_search(query, *, kb_id=None, topk=None, config=None):
        captured.update({"query": query, "kb_id": kb_id, "topk": topk})
        return search_result if search_result is not None else []

    monkeypatch.setattr(retriever, "search_chunks", _fake_search)
    monkeypatch.setattr(
        "server.storage.get_repository",
        lambda: type("Repo", (), {"get_knowledge_base": lambda self, kb_id: {"id": kb_id}})(),
    )
    return captured


def test_returns_numbered_chunks_for_citation(monkeypatch) -> None:
    _enable(monkeypatch, search_result=[_chunk("c1"), _chunk("c2")])

    payload = json.loads(tool.knowledge_search("报销流程"))

    assert payload["total"] == 2
    assert [c["num"] for c in payload["chunks"]] == [1, 2]
    assert payload["chunks"][0]["doc_name"] == "手册c1.pdf"
    assert payload["chunks"][0]["content"] == "正文 c1"


def test_empty_result_tells_model_not_to_fabricate(monkeypatch) -> None:
    _enable(monkeypatch, search_result=[])

    payload = json.loads(tool.knowledge_search("不存在的制度"))

    assert payload["total"] == 0
    assert payload["chunks"] == []
    assert "未检索到" in payload["message"]


def test_passes_kb_id_and_topk_through(monkeypatch) -> None:
    captured = _enable(monkeypatch, search_result=[_chunk("c1")])

    tool.knowledge_search("年假", kb_id="kb-9", topk=3)

    assert captured["kb_id"] == "kb-9"
    assert captured["topk"] == 3


def test_topk_clamped_to_max(monkeypatch) -> None:
    captured = _enable(monkeypatch, search_result=[])

    tool.knowledge_search("年假", topk=999)

    assert captured["topk"] == tool._MAX_TOPK


def test_unknown_kb_id_is_an_error(monkeypatch) -> None:
    _enable(monkeypatch, search_result=[])
    monkeypatch.setattr(
        "server.storage.get_repository",
        lambda: type("Repo", (), {"get_knowledge_base": lambda self, kb_id: None})(),
    )

    payload = json.loads(tool.knowledge_search("年假", kb_id="kb-missing"))

    assert "知识库不存在" in payload["error"]


def test_disabled_deployment_returns_error(monkeypatch) -> None:
    _enable(monkeypatch, config=_config(enabled=False))

    payload = json.loads(tool.knowledge_search("年假"))

    assert "未启用" in payload["error"]


def test_blank_query_is_an_error(monkeypatch) -> None:
    _enable(monkeypatch)

    payload = json.loads(tool.knowledge_search("   "))

    assert "非空" in payload["error"]


def test_retrieval_failure_becomes_tool_error(monkeypatch) -> None:
    _enable(monkeypatch)

    def _boom(query, *, kb_id=None, topk=None, config=None):
        raise retriever.RetrievalError("ES 与 Milvus 均不可用")

    monkeypatch.setattr(retriever, "search_chunks", _boom)

    payload = json.loads(tool._handle({"query": "报销"}))

    assert "检索失败" in payload["error"]


@pytest.mark.parametrize(
    ("enabled", "es_url", "milvus_uri", "embedding_url", "expected"),
    [
        (True, "http://es:9200", "http://milvus:19530", "http://llm/v1", True),
        (False, "http://es:9200", "http://milvus:19530", "http://llm/v1", False),
        (True, "", "http://milvus:19530", "http://llm/v1", False),
        (True, "http://es:9200", "", "http://llm/v1", False),
        (True, "http://es:9200", "http://milvus:19530", "", False),
    ],
)
def test_check_fn_requires_full_backend_config(
    monkeypatch, enabled, es_url, milvus_uri, embedding_url, expected
) -> None:
    cfg = KnowledgeDeploymentConfig(
        enabled=enabled,
        es_url=es_url,
        milvus_uri=milvus_uri,
        embedding=KnowledgeEmbeddingConfig(base_url=embedding_url, model="m", dim=2),
    )
    monkeypatch.setattr(tool, "_knowledge_config", lambda: cfg)

    assert tool._check_knowledge_enabled() is expected
