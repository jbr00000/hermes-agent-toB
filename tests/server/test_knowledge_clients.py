"""Client-trio tests: ES (fake HTTP session), Milvus (stub pymilvus), Embedder (fake OpenAI).

全部为 hermetic fake——不连真实 ES/Milvus/embedding 端点。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from server.deployment_config import KnowledgeDeploymentConfig, KnowledgeEmbeddingConfig
from server.knowledge import KnowledgeDisabledError
from server.knowledge import es_client as es_module
from server.knowledge import milvus_client as milvus_module
from server.knowledge.embedder import Embedder, EmbedderError


def _config(**overrides) -> KnowledgeDeploymentConfig:
    values = {
        "enabled": True,
        "mineru_url": "http://gpu-server:18888",
        "es_url": "http://elasticsearch:19200",
        "milvus_uri": "http://milvus:19530",
        "embedding": KnowledgeEmbeddingConfig(
            base_url="http://llm-gw.internal/v1", model="bge-m3", dim=4, batch_size=2
        ),
    }
    values.update(overrides)
    return KnowledgeDeploymentConfig(**values)


# ---------------------------------------------------------------- ES client


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"{}"):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    """Records requests; HEAD answers 404 (index missing) so create_index PUTs."""

    def __init__(self):
        self.calls = []
        self.auth = None
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "HEAD":
            return _FakeResponse(status_code=404, content=b"")
        if url.endswith("_bulk"):
            return _FakeResponse(payload={"items": [{"index": {"status": 200}}]})
        return _FakeResponse(payload={"deleted": 3})

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_es_session(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(es_module.requests, "Session", lambda: session)
    return session


def test_es_create_index_puts_mapping_when_missing(fake_es_session) -> None:
    client = es_module.ElasticsearchClient(base_url="http://es:19200/")
    client.create_index(index="knowledge_chunks", mappings=es_module.chunk_index_mappings())

    methods_urls = [(m, u) for m, u, _ in fake_es_session.calls]
    assert ("HEAD", "http://es:19200/knowledge_chunks") in methods_urls
    put = [c for c in fake_es_session.calls if c[0] == "PUT"][0]
    assert "chunk_content" in put[2]["json"]["mappings"]["properties"]


def test_es_bulk_insert_builds_ndjson_with_ids(fake_es_session) -> None:
    client = es_module.ElasticsearchClient(base_url="http://es:19200")
    result = client.bulk_insert(
        "knowledge_chunks",
        [{"id": "c1", "chunk_content": "你好"}, {"id": "c2", "chunk_content": "世界"}],
        id_field="id",
    )

    assert result == {"success": 2, "failed": 0, "total": 2}
    bulk = [c for c in fake_es_session.calls if c[1].endswith("_bulk")][0]
    lines = bulk[2]["data"].decode("utf-8").strip().split("\n")
    assert json.loads(lines[0]) == {"index": {"_index": "knowledge_chunks", "_id": "c1"}}
    assert json.loads(lines[1])["chunk_content"] == "你好"


def test_es_delete_by_term_uses_delete_by_query(fake_es_session) -> None:
    client = es_module.ElasticsearchClient(base_url="http://es:19200")
    client.delete_by_term("knowledge_chunks", "doc_id", "doc-1")

    call = fake_es_session.calls[-1]
    assert call[0] == "POST"
    assert "_delete_by_query" in call[1]
    assert call[2]["json"] == {"query": {"term": {"doc_id": "doc-1"}}}


def test_get_es_client_requires_enabled_and_url(monkeypatch) -> None:
    monkeypatch.setattr(es_module, "_CLIENT", None)
    monkeypatch.setattr(es_module, "_CLIENT_KEY", None)
    with pytest.raises(KnowledgeDisabledError):
        es_module.get_es_client(KnowledgeDeploymentConfig(enabled=False))
    with pytest.raises(KnowledgeDisabledError):
        es_module.get_es_client(_config(es_url=""))


# ------------------------------------------------------------- Milvus client


class _FakeInsertResult:
    def __init__(self, keys):
        self.primary_keys = keys


class _FakeCollection:
    created = []

    def __init__(self, name, schema=None, using=None):
        self.name = name
        self.schema = schema
        self.using = using
        self.inserted = []
        self.deleted_exprs = []
        self.flushed = False
        _FakeCollection.created.append(name)

    def create_index(self, field_name, index_params):
        self.index = (field_name, index_params)

    def insert(self, batch):
        self.inserted.extend(batch)
        return _FakeInsertResult([row["id"] for row in batch])

    def load(self):
        pass

    def delete(self, expr):
        self.deleted_exprs.append(expr)

    def flush(self):
        self.flushed = True


def _fake_pymilvus(monkeypatch, *, has_collection=False):
    module = types.ModuleType("pymilvus")
    module.connections = types.SimpleNamespace(connect=lambda **kw: None, disconnect=lambda a: None)
    existing = {"knowledge_chunks"} if has_collection else set()
    collections: dict[str, _FakeCollection] = {}

    def _get_or_create(name, schema=None, using=None):
        if name not in collections:
            collections[name] = _FakeCollection(name, schema, using)
        return collections[name]

    module.utility = types.SimpleNamespace(
        has_collection=lambda name, using=None: name in existing or name in collections,
        drop_collection=lambda name, using=None: None,
    )
    module.Collection = _get_or_create
    module.CollectionSchema = lambda fields, description="", enable_dynamic_field=False: fields
    _FakeCollection.created = []
    monkeypatch.setitem(sys.modules, "pymilvus", module)
    return module


def test_milvus_client_creates_collection_and_batches(monkeypatch) -> None:
    _fake_pymilvus(monkeypatch, has_collection=False)
    client = milvus_module.MilvusClient(uri="http://milvus:19530")

    client.create_collection("knowledge_chunks", ["f1"], "vector", {"index_type": "AUTOINDEX"})
    assert _FakeCollection.created == ["knowledge_chunks"]

    rows = [{"id": f"c{i}"} for i in range(3)]
    assert client.batch_insert_data("knowledge_chunks", rows, batch_size=2) == 3

    client.delete_by_doc_id("knowledge_chunks", 'doc"1')
    collection = client.get_collection("knowledge_chunks")
    assert collection.deleted_exprs == ['doc_id == "doc\\"1"']
    assert collection.flushed is True


def test_milvus_delete_on_missing_collection_is_noop(monkeypatch) -> None:
    _fake_pymilvus(monkeypatch, has_collection=False)
    client = milvus_module.MilvusClient(uri="milvus:19530")  # 无 scheme 也能解析
    client.delete_by_doc_id("knowledge_chunks", "doc-1")  # 不抛异常


def test_get_milvus_client_requires_enabled(monkeypatch) -> None:
    monkeypatch.setattr(milvus_module, "_CLIENT", None)
    monkeypatch.setattr(milvus_module, "_CLIENT_KEY", None)
    with pytest.raises(KnowledgeDisabledError):
        milvus_module.get_milvus_client(KnowledgeDeploymentConfig(enabled=False))


# ----------------------------------------------------------------- Embedder


class _FakeEmbeddingItem:
    def __init__(self, index, vector):
        self.index = index
        self.embedding = vector


class _FakeEmbeddings:
    def __init__(self, dim):
        self.dim = dim
        self.requests = []

    def create(self, *, model, input):
        self.requests.append(list(input))
        # 故意乱序返回，Embedder 应按 index 重排
        data = [
            _FakeEmbeddingItem(i, [float(i)] * self.dim) for i in reversed(range(len(input)))
        ]
        return types.SimpleNamespace(data=data)


def _embedder(monkeypatch, *, dim=4, batch_size=2) -> Embedder:
    fake = _FakeEmbeddings(4)  # 端点固定返回 4 维；Embedder 的期望维度由 dim 参数控制
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **kw: types.SimpleNamespace(embeddings=fake))
    embedder = Embedder(
        base_url="http://llm-gw.internal/v1", model="bge-m3", batch_size=batch_size, dim=dim
    )
    embedder._fake = fake  # type: ignore[attr-defined]
    return embedder


def test_embedder_batches_and_preserves_order(monkeypatch) -> None:
    embedder = _embedder(monkeypatch, batch_size=2)
    vectors = embedder.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert embedder._fake.requests == [["a", "b"], ["c"]]  # type: ignore[attr-defined]
    # OpenAI 返回的 index 是批次内序号；乱序返回被重排：批次1内 b 的向量是 [1]*4
    assert vectors[0] == [0.0] * 4
    assert vectors[1] == [1.0] * 4


def test_embedder_dim_mismatch_raises(monkeypatch) -> None:
    embedder = _embedder(monkeypatch, dim=8)  # fake 返回 dim=4
    with pytest.raises(EmbedderError, match="维度与配置不符"):
        embedder.embed(["a"])


def test_embedder_endpoint_failure_raises(monkeypatch) -> None:
    import openai

    def _boom(**kw):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kw: types.SimpleNamespace(
            embeddings=types.SimpleNamespace(create=lambda **ik: (_ for _ in ()).throw(ConnectionError("x")))
        ),
    )
    embedder = Embedder(base_url="http://x/v1", model="m", dim=1)
    with pytest.raises(EmbedderError, match="embedding 端点调用失败"):
        embedder.embed(["a"])
