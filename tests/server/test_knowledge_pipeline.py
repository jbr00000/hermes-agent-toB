"""Stage-4 pipeline tests: parse→chunk→persist→sync with hermetic fakes.

不连真实 MinerU/ES/Milvus/embedding——sync 三件套全部 monkeypatch 成记录型 fake。
"""
from __future__ import annotations

import time

import pytest

from server.deployment_config import KnowledgeDeploymentConfig, KnowledgeEmbeddingConfig
from server.storage import get_repository, init_storage, reset_storage_for_tests


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("HERMES_DATABASE_URL", "HERMES_TENANT_ID", "HERMES_CUSTOMER_ID"):
        monkeypatch.delenv(var, raising=False)
    reset_storage_for_tests()
    init_storage()
    return get_repository()


def _config(**overrides) -> KnowledgeDeploymentConfig:
    values = {
        "enabled": True,
        "mineru_url": "",
        "es_url": "http://es:19200",
        "milvus_uri": "http://milvus:19530",
        "embedding": KnowledgeEmbeddingConfig(
            base_url="http://llm-gw.internal/v1", model="bge-m3", dim=4, batch_size=8
        ),
        "chunk_size": 400,
        "chunk_overlap": 64,
    }
    values.update(overrides)
    return KnowledgeDeploymentConfig(**values)


@pytest.fixture()
def kb(repo):
    return repo.create_knowledge_base(name="规范库", creator_id="admin-1")


def _make_md_document(repo, kb, tmp_path, body: str, *, status: str = "pending") -> dict:
    files_dir = tmp_path / "knowledge" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    path = files_dir / "design.md"
    path.write_text(body, encoding="utf-8")
    document = repo.create_knowledge_document(
        kb_id=kb["id"],
        uploader_id="admin-1",
        title="设计规范",
        file_name="design.md",
        file_ext=".md",
        size_bytes=path.stat().st_size,
        file_path=str(path),
    )
    # 上传与解析已解耦：显式入队前文档是 uploaded；这里模拟 parse 接口的置位
    if status != "uploaded":
        repo.update_knowledge_document(document["id"], status=status)
    return repo.get_knowledge_document(document["id"])


_MD_BODY = "# 第一章 总则\n\n" + ("氢电系统设计需要遵循的基本准则与术语定义。" * 10) + "\n\n# 第二章 参数\n\n" + ("关键参数的选取方法与校核流程。" * 10)


def test_pipeline_md_document_reaches_ready(repo, kb, tmp_path, monkeypatch) -> None:
    from server.knowledge import pipeline

    synced: list[str] = []
    monkeypatch.setattr(
        pipeline, "synchronize_document", lambda doc_id, *, config=None: synced.append(doc_id) or 1
    )
    document = _make_md_document(repo, kb, tmp_path, _MD_BODY)
    job = {"job_id": "job-1", "doc_id": document["id"], "user_id": "admin-1"}

    assert pipeline.run_job(job, "worker-1", config=_config()) == "succeeded"

    updated = repo.get_knowledge_document(document["id"])
    assert updated["status"] == "ready"
    assert updated["parser"] == "local"
    assert updated["chunk_count"] >= 2
    assert updated["finished_at"]
    chunks = repo.list_knowledge_chunks(document["id"])
    assert [c["doc_pos"] for c in chunks] == list(range(len(chunks)))
    assert {c["doc_name"] for c in chunks} == {"design.md"}
    assert all(c["token_num"] > 0 for c in chunks)
    assert synced == [document["id"]]


def test_pipeline_is_idempotent_on_rerun(repo, kb, tmp_path, monkeypatch) -> None:
    from server.knowledge import pipeline

    monkeypatch.setattr(
        pipeline, "synchronize_document", lambda doc_id, *, config=None: 1
    )
    document = _make_md_document(repo, kb, tmp_path, _MD_BODY)
    job = {"job_id": "job-1", "doc_id": document["id"], "user_id": "admin-1"}

    pipeline.run_job(job, "worker-1", config=_config())
    first = repo.list_knowledge_chunks(document["id"])
    pipeline.run_job(job, "worker-1", config=_config())  # 重跑（retry/recovery）
    second = repo.list_knowledge_chunks(document["id"])

    assert len(first) == len(second)
    assert [c["content"] for c in first] == [c["content"] for c in second]


def test_pipeline_marks_failed_on_parse_error(repo, kb, tmp_path, monkeypatch) -> None:
    from server.knowledge import pipeline

    # pdf 且未配置 mineru_url → ParseError
    files_dir = tmp_path / "knowledge" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    path = files_dir / "paper.pdf"
    path.write_bytes(b"%PDF fake")
    document = repo.create_knowledge_document(
        kb_id=kb["id"],
        uploader_id="admin-1",
        title="论文",
        file_name="paper.pdf",
        file_ext=".pdf",
        size_bytes=path.stat().st_size,
        file_path=str(path),
    )
    repo.update_knowledge_document(document["id"], status="pending")
    job_row = repo.create_knowledge_job(doc_id=document["id"], user_id="admin-1")
    assert repo.list_stale_running_knowledge_jobs(time.time() + 1) == []  # queued ≠ running stale

    job = {"job_id": job_row["id"], "doc_id": document["id"], "user_id": "admin-1"}
    assert pipeline.run_job(job, "worker-1", config=_config()) == "failed"

    updated = repo.get_knowledge_document(document["id"])
    assert updated["status"] == "failed"
    assert "mineru_url" in updated["error"]
    failed_job = repo.get_knowledge_job(job["job_id"])
    assert failed_job["status"] == "failed"
    assert failed_job["finished_at"]


def test_pipeline_failed_when_document_missing(repo) -> None:
    from server.knowledge import pipeline

    job = {"job_id": "job-x", "doc_id": "doc-not-exists", "user_id": "u"}
    # doc 不存在 → failed，不抛异常
    assert pipeline.run_job(job, "worker-1", config=_config()) == "failed"


def test_pipeline_refuses_document_not_pending(repo, kb, tmp_path) -> None:
    """uploaded 文档的 job 属于误入队：job 失败终结，文档状态保持 uploaded。"""
    from server.knowledge import pipeline

    document = _make_md_document(repo, kb, tmp_path, _MD_BODY, status="uploaded")
    job_row = repo.create_knowledge_job(doc_id=document["id"], user_id="admin-1")
    job = {"job_id": job_row["id"], "doc_id": document["id"], "user_id": "admin-1"}

    assert pipeline.run_job(job, "worker-1", config=_config()) == "failed"

    assert repo.get_knowledge_document(document["id"])["status"] == "uploaded"
    assert repo.list_knowledge_chunks(document["id"]) == []
    failed_job = repo.get_knowledge_job(job_row["id"])
    assert failed_job["status"] == "failed"


# ------------------------------------------------------------ sync_service


class _FakeEs:
    def __init__(self, calls: list):
        self.calls = calls

    def create_index(self, *, index, mappings, settings=None):
        self.calls.append(("es.create_index", index))

    def delete_by_term(self, index, field, value):
        self.calls.append(("es.delete", field, value))

    def bulk_insert(self, index, docs, *, id_field=None):
        self.calls.append(("es.bulk", [d["id"] for d in docs], docs))
        return {"success": len(docs), "failed": 0, "total": len(docs)}


class _FakeMilvus:
    def __init__(self, calls: list):
        self.calls = calls

    def delete_by_doc_id(self, collection, doc_id):
        self.calls.append(("milvus.delete", doc_id))

    def create_collection(self, name, fields, field_names, index_params):
        self.calls.append(("milvus.create", name))

    def batch_insert_data(self, collection, rows, batch_size=1000):
        self.calls.append(
            ("milvus.insert", [r["id"] for r in rows], len(rows[0]["vector"]), rows)
        )
        return len(rows)


class _FakeEmbedder:
    def embed(self, texts):
        return [[float(i)] * 4 for i, _ in enumerate(texts)]


def test_sync_service_delete_then_write_both_engines(repo, kb, tmp_path, monkeypatch) -> None:
    from server.knowledge import sync_service

    document = _make_md_document(repo, kb, tmp_path, _MD_BODY)
    repo.replace_knowledge_chunks(
        document["id"],
        "design.md",
        [
            {"chunk_title": "第一章", "content": "内容甲", "doc_pos": 0, "token_num": 10},
            {"chunk_title": "第二章", "content": "内容乙", "doc_pos": 1, "token_num": 10},
        ],
    )
    calls: list = []
    monkeypatch.setattr(sync_service, "get_es_client", lambda cfg=None: _FakeEs(calls))
    monkeypatch.setattr(sync_service, "get_milvus_client", lambda cfg=None: _FakeMilvus(calls))
    monkeypatch.setattr(sync_service, "get_embedder", lambda cfg=None: _FakeEmbedder())

    count = sync_service.synchronize_document(document["id"], config=_config())

    assert count == 2
    kinds = [c[0] for c in calls]
    # 先删后写，两个引擎都如此
    assert kinds.index("es.delete") < kinds.index("es.bulk")
    assert kinds.index("milvus.delete") < kinds.index("milvus.insert")
    insert = next(c for c in calls if c[0] == "milvus.insert")
    assert insert[2] == 4  # 向量维度与配置一致
    # 投影 payload 冗余 kb_id（后续按库检索过滤用）
    assert {r["kb_id"] for r in insert[3]} == {kb["id"]}
    bulk = next(c for c in calls if c[0] == "es.bulk")
    assert len(bulk[1]) == 2
    assert {d["kb_id"] for d in bulk[2]} == {kb["id"]}

    # 重跑幂等：再次 delete 先于再次 bulk
    calls.clear()
    sync_service.synchronize_document(document["id"], config=_config())
    kinds = [c[0] for c in calls]
    assert kinds.index("es.delete") < kinds.index("es.bulk")


def test_sync_service_empty_document_only_deletes(repo, kb, monkeypatch) -> None:
    from server.knowledge import sync_service

    document = repo.create_knowledge_document(
        kb_id=kb["id"],
        uploader_id="admin-1",
        title="空文档",
        file_name="empty.md",
        file_ext=".md",
        size_bytes=0,
        file_path="/nonexistent",
    )
    calls: list = []
    monkeypatch.setattr(sync_service, "get_es_client", lambda cfg=None: _FakeEs(calls))
    monkeypatch.setattr(sync_service, "get_milvus_client", lambda cfg=None: _FakeMilvus(calls))

    assert sync_service.synchronize_document(document["id"], config=_config()) == 0
    kinds = [c[0] for c in calls]
    assert "es.delete" in kinds and "milvus.delete" in kinds
    assert "es.bulk" not in kinds and "milvus.insert" not in kinds
