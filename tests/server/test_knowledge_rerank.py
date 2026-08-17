"""Rerank client + retriever 精排接入测试（hermetic fake，无网络）。"""
from __future__ import annotations

import pytest

from server.deployment_config import (
    KnowledgeDeploymentConfig,
    KnowledgeEmbeddingConfig,
    KnowledgeRerankConfig,
)
from server.knowledge import rerank_client, retriever


def _config(*, rerank: bool = True, rerank_top_k: int = 12) -> KnowledgeDeploymentConfig:
    return KnowledgeDeploymentConfig(
        enabled=True,
        es_url="http://elasticsearch:19200",
        milvus_uri="http://milvus:19530",
        embedding=KnowledgeEmbeddingConfig(
            base_url="http://llm-gw.internal/v1", model="bge-m3", dim=2
        ),
        rerank=(
            KnowledgeRerankConfig(
                base_url="http://llm-gw.internal/v1",
                model="bge-reranker-v2-m3",
                top_k=rerank_top_k,
            )
            if rerank
            else KnowledgeRerankConfig()
        ),
    )


class _FakeEsClient:
    def __init__(self, hits):
        self._hits = hits

    def search(self, *, index, body):
        return {
            "hits": {
                "hits": [{"_id": chunk_id, "_score": score} for chunk_id, score in self._hits]
            }
        }


class _FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _FakeMilvusClient:
    def __init__(self, hits):
        self._hits = hits

    def search(self, collection_name, query_vector, *, topk, kb_id=None):
        return [{"id": chunk_id, "score": score} for chunk_id, score in self._hits]


class _FakeRepository:
    def __init__(self, rows):
        self._rows = rows
        self.requested_ids: list[str] = []

    def get_knowledge_chunks_by_ids(self, chunk_ids):
        self.requested_ids = list(chunk_ids)
        return [self._rows[cid] for cid in chunk_ids if cid in self._rows]


def _chunk_row(chunk_id):
    return {
        "id": chunk_id,
        "kb_id": "kb-1",
        "doc_id": f"doc-{chunk_id}",
        "doc_name": f"文档{chunk_id}.pdf",
        "chunk_title": f"标题{chunk_id}",
        "content": f"MySQL 全文 {chunk_id}",
        "doc_pos": 0,
        "token_num": 10,
        "is_use": True,
        "created_at": None,
    }


class _FakeReranker:
    """按预设分数表打分；记录收到的 documents 以验证全文回表在先。"""

    def __init__(self, scores_by_content: dict[str, float], *, boom: bool = False):
        self._scores = scores_by_content
        self._boom = boom
        self.calls: list[dict] = []

    def rerank(self, query, documents, *, top_n=None):
        self.calls.append({"query": query, "documents": list(documents), "top_n": top_n})
        if self._boom:
            raise rerank_client.RerankError("rerank down")
        return [self._scores.get(doc, 0.0) for doc in documents]


def _wire(monkeypatch, *, es, milvus, repo, reranker=None):
    monkeypatch.setattr(retriever, "get_es_client", lambda config: es)
    monkeypatch.setattr(retriever, "get_embedder", lambda config: _FakeEmbedder())
    monkeypatch.setattr(retriever, "get_milvus_client", lambda config: milvus)
    monkeypatch.setattr(retriever, "get_repository", lambda: repo)
    if reranker is not None:
        monkeypatch.setattr(retriever, "get_reranker", lambda config: reranker)


def test_rerank_reorders_fused_candidates_and_scores(monkeypatch) -> None:
    """融合序 c1>c2>c3，rerank 认为 c3 最相关 → 输出按 rerank 分重排。"""
    reranker = _FakeReranker(
        {"MySQL 全文 c1": 0.1, "MySQL 全文 c2": 0.5, "MySQL 全文 c3": 0.9}
    )
    repo = _FakeRepository({cid: _chunk_row(cid) for cid in ("c1", "c2", "c3")})
    _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0), ("c2", 3.0), ("c3", 1.0)]),
        milvus=_FakeMilvusClient([]),
        repo=repo,
        reranker=reranker,
    )

    results = retriever.search_chunks("报销流程", config=_config())

    assert [item["chunk_id"] for item in results] == ["c3", "c2", "c1"]
    # score 换成 rerank 相关度分
    assert results[0]["score"] == pytest.approx(0.9)
    # rerank 输入是 MySQL 回表全文（不是 ES/Milvus 投影）
    assert reranker.calls[0]["documents"] == [
        "MySQL 全文 c1",
        "MySQL 全文 c2",
        "MySQL 全文 c3",
    ]


def test_rerank_pool_follows_rerank_top_k_then_cut_to_topk(monkeypatch) -> None:
    """回表池 = rerank.top_k（融合序截取），最终输出仍截到 retrieval.topk。"""
    reranker = _FakeReranker({f"MySQL 全文 c{i}": float(i) for i in range(1, 6)})
    repo = _FakeRepository({f"c{i}": _chunk_row(f"c{i}") for i in range(1, 6)})
    _wire(
        monkeypatch,
        es=_FakeEsClient([(f"c{i}", 10.0 - i) for i in range(1, 6)]),
        milvus=_FakeMilvusClient([]),
        repo=repo,
        reranker=reranker,
    )

    cfg = _config(rerank_top_k=3)
    results = retriever.search_chunks("报销", topk=2, config=cfg)

    assert repo.requested_ids == ["c1", "c2", "c3"]  # 池 = rerank.top_k=3
    assert [item["chunk_id"] for item in results] == ["c3", "c2"]  # 截到 topk=2


def test_rerank_pool_never_smaller_than_topk(monkeypatch) -> None:
    """rerank.top_k 配得比 retrieval.topk 还小时，池至少要有 topk 条（否则凑不齐）。"""
    reranker = _FakeReranker({f"MySQL 全文 c{i}": float(i) for i in range(1, 6)})
    repo = _FakeRepository({f"c{i}": _chunk_row(f"c{i}") for i in range(1, 6)})
    _wire(
        monkeypatch,
        es=_FakeEsClient([(f"c{i}", 10.0 - i) for i in range(1, 6)]),
        milvus=_FakeMilvusClient([]),
        repo=repo,
        reranker=reranker,
    )

    cfg = _config(rerank_top_k=2)
    results = retriever.search_chunks("报销", topk=4, config=cfg)

    assert repo.requested_ids == ["c1", "c2", "c3", "c4"]  # 池 = max(topk=4, top_k=2)
    assert len(results) == 4  # 凑齐 topk


def test_rerank_failure_falls_back_to_fused_order(monkeypatch) -> None:
    reranker = _FakeReranker({}, boom=True)
    repo = _FakeRepository({cid: _chunk_row(cid) for cid in ("c1", "c2")})
    _wire(
        monkeypatch,
        es=_FakeEsClient([("c1", 5.0), ("c2", 3.0)]),
        milvus=_FakeMilvusClient([]),
        repo=repo,
        reranker=reranker,
    )

    results = retriever.search_chunks("报销", config=_config())
    assert [item["chunk_id"] for item in results] == ["c1", "c2"]


def test_rerank_unconfigured_keeps_fused_path(monkeypatch) -> None:
    """未配置 rerank：回表池 = topk，不调用 reranker（原路径回归）。"""
    repo = _FakeRepository({f"c{i}": _chunk_row(f"c{i}") for i in range(1, 5)})
    _wire(
        monkeypatch,
        es=_FakeEsClient([(f"c{i}", 10.0 - i) for i in range(1, 5)]),
        milvus=_FakeMilvusClient([]),
        repo=repo,
    )

    results = retriever.search_chunks("报销", topk=2, config=_config(rerank=False))

    assert repo.requested_ids == ["c1", "c2"]  # 只回表 topk 个
    assert [item["chunk_id"] for item in results] == ["c1", "c2"]


def test_reranker_parses_jina_style_response() -> None:
    """Reranker.rerank 按 results[{index, relevance_score}] 对齐输入序。"""

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }

    class _FakeHttp:
        def __init__(self):
            self.bodies: list[dict] = []

        def post(self, url, json):
            self.bodies.append(json)
            return _FakeResponse()

    reranker = rerank_client.Reranker(
        base_url="http://x.internal/v1", model="bge-reranker-v2-m3"
    )
    fake_http = _FakeHttp()
    reranker._client = fake_http

    scores = reranker.rerank("q", ["doc-a", "doc-b"], top_n=2)

    assert scores == [0.2, 0.7]
    assert fake_http.bodies[0]["documents"] == ["doc-a", "doc-b"]
    assert fake_http.bodies[0]["top_n"] == 2


def test_reranker_malformed_payload_raises() -> None:
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": True}

    class _FakeHttp:
        def post(self, url, json):
            return _FakeResponse()

    reranker = rerank_client.Reranker(base_url="http://x.internal/v1", model="m")
    reranker._client = _FakeHttp()

    with pytest.raises(rerank_client.RerankError):
        reranker.rerank("q", ["doc-a"])


def test_rerank_configured_requires_full_config() -> None:
    assert rerank_client.rerank_configured(_config()) is True
    assert rerank_client.rerank_configured(_config(rerank=False)) is False
    assert rerank_client.rerank_configured(KnowledgeDeploymentConfig(enabled=False)) is False


def test_deployment_config_parses_rerank_and_aux_llm_sections(tmp_path) -> None:
    from server.deployment_config import load_deployment_config

    path = tmp_path / "deployment.yaml"
    path.write_text(
        "knowledge:\n"
        "  enabled: true\n"
        "  rerank:\n"
        "    base_url: http://gpu.internal:9997/v1/\n"
        "    model: bge-reranker-v2-m3\n"
        "    top_k: 12\n"
        "  aux_llm:\n"
        "    base_url: http://gpu.internal:5002/v1\n"
        "    model: qwen-27B-FP8\n",
        encoding="utf-8",
    )
    cfg = load_deployment_config(path)
    assert cfg.knowledge.rerank.base_url == "http://gpu.internal:9997/v1"  # 尾斜杠剥掉
    assert cfg.knowledge.rerank.model == "bge-reranker-v2-m3"
    assert cfg.knowledge.rerank.top_k == 12
    assert cfg.knowledge.rerank.api_key_env == "KNOWLEDGE_RERANK_API_KEY"
    assert cfg.knowledge.aux_llm.base_url == "http://gpu.internal:5002/v1"
    assert cfg.knowledge.aux_llm.model == "qwen-27B-FP8"
    assert cfg.knowledge.aux_llm.api_key_env == "KNOWLEDGE_AUX_LLM_API_KEY"


def test_deployment_config_rerank_base_url_accepts_full_rerank_path(tmp_path) -> None:
    """直接粘贴 .../v1/rerank 全路径也不会拼出 /rerank/rerank。"""
    from server.deployment_config import load_deployment_config

    path = tmp_path / "deployment.yaml"
    path.write_text(
        "knowledge:\n"
        "  rerank:\n"
        "    base_url: http://gpu.internal:9997/v1/rerank\n"
        "    model: bge-reranker-v2-m3\n",
        encoding="utf-8",
    )
    cfg = load_deployment_config(path)
    assert cfg.knowledge.rerank.base_url == "http://gpu.internal:9997/v1"


def test_deployment_config_rerank_aux_defaults_when_absent(tmp_path) -> None:
    from server.deployment_config import load_deployment_config

    cfg = load_deployment_config(tmp_path / "missing.yaml")
    assert cfg.knowledge.rerank.base_url == ""
    assert cfg.knowledge.rerank.top_k == 12
    assert cfg.knowledge.aux_llm.base_url == ""
