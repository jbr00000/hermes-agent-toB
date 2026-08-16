"""Hybrid retriever tests — hermetic fakes for ES / Milvus / embedder / repository."""
from __future__ import annotations

import pytest

from server.deployment_config import KnowledgeDeploymentConfig, KnowledgeEmbeddingConfig
from server.knowledge import retriever


def _config() -> KnowledgeDeploymentConfig:
    return KnowledgeDeploymentConfig(
        enabled=True,
        es_url="http://elasticsearch:19200",
        milvus_uri="http://milvus:19530",
        embedding=KnowledgeEmbeddingConfig(
            base_url="http://llm-gw.internal/v1", model="bge-m3", dim=2
        ),
    )


class _FakeEsClient:
    def __init__(self, hits, *, boom=False):
        self._hits = hits
        self._boom = boom
        self.bodies: list[dict] = []

    def search(self, *, index, body):
        self.bodies.append(body)
        if self._boom:
            raise ConnectionError("es down")
        return {
            "hits": {
                "hits": [{"_id": chunk_id, "_score": score} for chunk_id, score in self._hits]
            }
        }


class _FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _FakeMilvusClient:
    def __init__(self, hits, *, boom=False):
        self._hits = hits
        self._boom = boom
        self.calls: list[dict] = []

    def search(self, collection_name, query_vector, *, topk, kb_id=None):
        self.calls.append(
            {"collection": collection_name, "topk": topk, "kb_id": kb_id, "vector": query_vector}
        )
        if self._boom:
            raise ConnectionError("milvus down")
        return [
            {"id": chunk_id, "score": score, "doc_id": "", "chunk_title": ""}
            for chunk_id, score in self._hits
        ]


class _FakeRepository:
    def __init__(self, rows):
        self._rows = rows
        self.requested_ids: list[str] = []

    def get_knowledge_chunks_by_ids(self, chunk_ids):
        self.requested_ids = list(chunk_ids)
        return [self._rows[cid] for cid in chunk_ids if cid in self._rows]


def _chunk_row(chunk_id, *, is_use=True, content=None):
    return {
        "id": chunk_id,
        "kb_id": "kb-1",
        "doc_id": f"doc-{chunk_id}",
        "doc_name": f"文档{chunk_id}.pdf",
        "chunk_title": f"标题{chunk_id}",
        "content": content if content is not None else f"MySQL 全文 {chunk_id}",
        "doc_pos": 0,
        "token_num": 10,
        "is_use": is_use,
        "created_at": None,
    }


def _wire(monkeypatch, *, es=None, milvus=None, repo=None):
    monkeypatch.setattr(retriever, "get_es_client", lambda config: es)
    monkeypatch.setattr(retriever, "get_embedder", lambda config: _FakeEmbedder())
    monkeypatch.setattr(retriever, "get_milvus_client", lambda config: milvus)
    monkeypatch.setattr(retriever, "get_repository", lambda: repo)
    return es, milvus, repo


def test_search_chunks_fuses_both_paths_and_hydrates_from_mysql(monkeypatch) -> None:
    es, milvus, repo = _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0), ("c2", 3.0)]),
        milvus=_FakeMilvusClient([("c2", 0.9), ("c3", 0.8)]),
        repo=_FakeRepository({cid: _chunk_row(cid) for cid in ("c1", "c2", "c3")}),
    )

    results = retriever.search_chunks("报销流程", kb_id="kb-1", config=_config())

    # c2 两路都命中（RRF 叠加）→ 第一；c3 向量分高 → 第二；c1 仅 ES → 第三
    assert [item["chunk_id"] for item in results] == ["c2", "c3", "c1"]
    # 正文必须来自 MySQL 回表（ES/Milvus 投影里没有完整 content）
    assert results[0]["content"] == "MySQL 全文 c2"
    assert results[0]["doc_name"] == "文档c2.pdf"
    assert repo.requested_ids == ["c2", "c3", "c1"]  # 回表顺序 = 融合顺序

    # ES 查询体：is_use + kb_id 过滤、三个文本字段
    body = es.bodies[0]
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"is_use": "true"}} in filters
    assert {"term": {"kb_id": "kb-1"}} in filters
    multi = body["query"]["bool"]["must"][0]["multi_match"]
    assert set(multi["fields"]) == {"chunk_title^2", "chunk_content", "doc_name"}
    # Milvus 路收到 kb 过滤
    assert milvus.calls[0]["kb_id"] == "kb-1"


def test_search_chunks_without_kb_id_omits_kb_filters(monkeypatch) -> None:
    es, milvus, _ = _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0)]),
        milvus=_FakeMilvusClient([]),
        repo=_FakeRepository({"c1": _chunk_row("c1")}),
    )

    results = retriever.search_chunks("年假", config=_config())

    assert [item["chunk_id"] for item in results] == ["c1"]
    filters = es.bodies[0]["query"]["bool"]["filter"]
    assert filters == [{"term": {"is_use": "true"}}]
    assert milvus.calls[0]["kb_id"] is None


def test_search_chunks_degrades_to_vector_when_es_fails(monkeypatch) -> None:
    _wire(
        monkeypatch,
        es=_FakeEsClient([], boom=True),
        milvus=_FakeMilvusClient([("c9", 0.95)]),
        repo=_FakeRepository({"c9": _chunk_row("c9")}),
    )

    results = retriever.search_chunks("出差标准", config=_config())
    assert [item["chunk_id"] for item in results] == ["c9"]


def test_search_chunks_raises_when_both_backends_fail(monkeypatch) -> None:
    _wire(
        monkeypatch,
        es=_FakeEsClient([], boom=True),
        milvus=_FakeMilvusClient([], boom=True),
        repo=_FakeRepository({}),
    )

    with pytest.raises(retriever.RetrievalError):
        retriever.search_chunks("报销", config=_config())


def test_search_chunks_filters_disabled_chunks_after_hydration(monkeypatch) -> None:
    _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0), ("c2", 4.0)]),
        milvus=_FakeMilvusClient([]),
        repo=_FakeRepository(
            {"c1": _chunk_row("c1", is_use=False), "c2": _chunk_row("c2")}
        ),
    )

    results = retriever.search_chunks("保密制度", config=_config())
    assert [item["chunk_id"] for item in results] == ["c2"]


def test_search_chunks_empty_query_short_circuits(monkeypatch) -> None:
    es, milvus, _ = _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0)]),
        milvus=_FakeMilvusClient([("c1", 0.9)]),
        repo=_FakeRepository({"c1": _chunk_row("c1")}),
    )

    assert retriever.search_chunks("   ", config=_config()) == []
    assert es.bodies == [] and milvus.calls == []


def test_normalize_gives_no_free_score_to_missing_path() -> None:
    # 整路缺失（全 0）时归一化保持全 0——否则缺路结果会被白送 1.0 分
    assert retriever._normalize([0.0, 0.0]) == [0.0, 0.0]
    assert retriever._normalize([]) == []
    assert retriever._normalize([2.0, 2.0]) == [1.0, 1.0]
    assert retriever._normalize([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]


def test_deployment_config_parses_retrieval_section(tmp_path) -> None:
    from server.deployment_config import load_deployment_config

    path = tmp_path / "deployment.yaml"
    path.write_text(
        "knowledge:\n"
        "  enabled: true\n"
        "  retrieval:\n"
        "    topk: 4\n"
        "    es_candidates: 50\n"
        "    vector_weight: 0.7\n",
        encoding="utf-8",
    )
    cfg = load_deployment_config(path)
    assert cfg.knowledge.retrieval.topk == 4
    assert cfg.knowledge.retrieval.es_candidates == 50
    assert cfg.knowledge.retrieval.vector_weight == 0.7
    # 未配置的项回落默认
    assert cfg.knowledge.retrieval.rrf_k == 60
    assert cfg.knowledge.retrieval.vector_candidates == 30


def test_deployment_config_retrieval_defaults_when_absent(tmp_path) -> None:
    from server.deployment_config import load_deployment_config

    cfg = load_deployment_config(tmp_path / "missing.yaml")
    assert cfg.knowledge.retrieval.topk == 6
    assert cfg.knowledge.retrieval.vector_weight == 0.6
