"""Storage-layer tests for the knowledge base tables.

MySQL 是事实源：知识库（knowledge_bases）是文档的分组实体；documents 走状态机
（uploaded→pending→parsing→syncing→ready/failed，上传与解析已解耦），chunks 可
整体替换（幂等重解析），jobs 支撑 worker 恢复。ES/Milvus 不在这里测。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.fixture()
def repo(monkeypatch, tmp_path):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_TENANT_ID", raising=False)
    monkeypatch.delenv("HERMES_CUSTOMER_ID", raising=False)

    from server.storage import get_repository, init_storage, reset_storage_for_tests

    reset_storage_for_tests()
    init_storage()
    return get_repository()


@pytest.fixture()
def kb(repo):
    return repo.create_knowledge_base(name="运维规范库", creator_id="u-admin")


def _create_doc(repo, kb, **overrides):
    values = {
        "kb_id": kb["id"],
        "uploader_id": "u-admin",
        "title": "员工手册",
        "file_name": "员工手册.pdf",
        "file_ext": ".pdf",
        "size_bytes": 1024,
        "file_path": "knowledge/files/doc-1.pdf",
    }
    values.update(overrides)
    return repo.create_knowledge_document(**values)


def test_base_crud_and_unique_name(repo) -> None:
    base = repo.create_knowledge_base(
        name="制度库", creator_id="u-admin", description="公司制度文件"
    )
    assert base["doc_count"] == 0
    assert base["chunk_count"] == 0
    assert repo.get_knowledge_base(base["id"])["name"] == "制度库"
    assert repo.get_knowledge_base_by_name("制度库")["id"] == base["id"]
    assert [b["id"] for b in repo.list_knowledge_bases()] == [base["id"]]

    renamed = repo.update_knowledge_base(base["id"], name="制度库V2", description=None)
    assert renamed["name"] == "制度库V2"
    assert repo.get_knowledge_base_by_name("制度库") is None

    with pytest.raises(IntegrityError):
        repo.create_knowledge_base(name="制度库V2", creator_id="u-admin")

    assert repo.get_knowledge_base("missing") is None
    assert repo.update_knowledge_base("missing", name="x") is None
    assert repo.delete_knowledge_base("missing") is None


def test_default_base_is_lazily_created_once(repo) -> None:
    first = repo.get_or_create_default_knowledge_base(creator_id="u-admin")
    second = repo.get_or_create_default_knowledge_base(creator_id="u-admin")
    assert first["id"] == second["id"]
    assert len(repo.list_knowledge_bases()) == 1


def test_uploaded_document_waits_for_parse_and_counts_track(repo, kb) -> None:
    doc = _create_doc(repo, kb)
    # 上传只到 uploaded —— 解析由显式入队触发（routes 层），存储层不自动推进
    assert doc["status"] == "uploaded"
    assert doc["kb_id"] == kb["id"]
    assert repo.get_knowledge_base(kb["id"])["doc_count"] == 1

    queued = repo.update_knowledge_document(doc["id"], status="pending")
    assert queued["status"] == "pending"

    repo.replace_knowledge_chunks(
        doc["id"], doc["file_name"], [{"content": "x", "doc_pos": 0, "token_num": 1}]
    )
    base = repo.get_knowledge_base(kb["id"])
    assert base["chunk_count"] == 1

    chunks = repo.list_knowledge_chunks(doc["id"])
    assert chunks[0]["kb_id"] == kb["id"]


def test_document_lifecycle_and_stats(repo, kb) -> None:
    doc = _create_doc(repo, kb)
    assert doc["chunk_count"] == 0

    updated = repo.update_knowledge_document(doc["id"], status="parsing", parser="mineru")
    assert updated["status"] == "parsing"
    assert updated["parser"] == "mineru"

    done = repo.update_knowledge_document(
        doc["id"], status="ready", chunk_count=7, finished_at=time.time()
    )
    assert done["chunk_count"] == 7
    assert done["finished_at"] is not None

    assert repo.knowledge_stats() == {"documents": 1, "chunks": 7}

    assert [d["id"] for d in repo.list_knowledge_documents()] == [doc["id"]]
    assert [d["id"] for d in repo.list_knowledge_documents(kb_id=kb["id"])] == [doc["id"]]
    assert repo.list_knowledge_documents(kb_id="other-kb") == []
    assert repo.list_knowledge_documents(status="failed") == []
    assert repo.get_knowledge_document("missing") is None
    assert repo.update_knowledge_document("missing", status="ready") is None


def test_replace_knowledge_chunks_is_idempotent(repo, kb) -> None:
    doc = _create_doc(repo, kb)
    first = [
        {"content": "第一段", "doc_pos": 0, "chunk_title": "总则", "token_num": 10},
        {"content": "第二段", "doc_pos": 1, "chunk_title": "总则", "token_num": 12},
    ]
    assert repo.replace_knowledge_chunks(doc["id"], doc["file_name"], first) == 2
    ids_v1 = [c["id"] for c in repo.list_knowledge_chunks(doc["id"])]
    assert len(ids_v1) == 2

    # 重解析：整体替换，旧 chunk 不残留，库计数跟着走
    second = [{"content": "新内容", "doc_pos": 0, "chunk_title": "总则", "token_num": 8}]
    assert repo.replace_knowledge_chunks(doc["id"], doc["file_name"], second) == 1
    chunks = repo.list_knowledge_chunks(doc["id"])
    assert [c["content"] for c in chunks] == ["新内容"]
    assert chunks[0]["id"] not in ids_v1
    assert chunks[0]["doc_name"] == doc["file_name"]
    assert repo.get_knowledge_base(kb["id"])["chunk_count"] == 1


def test_delete_document_cascades_chunks_and_jobs(repo, kb) -> None:
    doc = _create_doc(repo, kb)
    repo.replace_knowledge_chunks(
        doc["id"], doc["file_name"], [{"content": "x", "doc_pos": 0, "token_num": 1}]
    )
    job = repo.create_knowledge_job(doc_id=doc["id"], user_id="u-admin")

    assert repo.delete_knowledge_document(doc["id"]) is True

    assert repo.get_knowledge_document(doc["id"]) is None
    assert repo.list_knowledge_chunks(doc["id"]) == []
    assert repo.get_knowledge_job(job["id"]) is None
    assert repo.delete_knowledge_document(doc["id"]) is False

    base = repo.get_knowledge_base(kb["id"])
    assert base["doc_count"] == 0
    assert base["chunk_count"] == 0


def test_delete_base_cascades_and_returns_docs(repo, kb) -> None:
    doc_a = _create_doc(repo, kb)
    doc_b = _create_doc(repo, kb, file_name="b.pdf", file_path="knowledge/files/b.pdf")
    repo.replace_knowledge_chunks(
        doc_a["id"], doc_a["file_name"], [{"content": "x", "doc_pos": 0, "token_num": 1}]
    )
    repo.create_knowledge_job(doc_id=doc_b["id"], user_id="u-admin")

    deleted = repo.delete_knowledge_base(kb["id"])
    assert deleted is not None
    assert {d["id"] for d in deleted} == {doc_a["id"], doc_b["id"]}
    # 调用方需要 file_path 清理磁盘 —— 返回的文档必须带着它
    assert all(d["file_path"] for d in deleted)

    assert repo.get_knowledge_base(kb["id"]) is None
    assert repo.list_knowledge_documents() == []
    assert repo.list_knowledge_chunks(doc_a["id"]) == []
    assert repo.knowledge_stats() == {"documents": 0, "chunks": 0}


def test_knowledge_job_state_and_stale_recovery(repo, kb) -> None:
    doc = _create_doc(repo, kb)
    job = repo.create_knowledge_job(doc_id=doc["id"], user_id="u-admin")
    assert job["status"] == "queued"
    assert job["attempt"] == 0

    stale = time.time() - 600
    running = repo.update_knowledge_job(
        job["id"], status="running", attempt=1, worker_id="w1", heartbeat_at=stale
    )
    assert running["status"] == "running"

    recovered = repo.list_stale_running_knowledge_jobs(stale_before=time.time() - 300)
    assert [j["id"] for j in recovered] == [job["id"]]

    repo.update_knowledge_job(job["id"], status="succeeded", finished_at=time.time())
    assert repo.list_stale_running_knowledge_jobs(stale_before=time.time() - 300) == []


def test_bases_and_documents_are_tenant_scoped(repo, kb, monkeypatch) -> None:
    doc = _create_doc(repo, kb)

    monkeypatch.setenv("HERMES_TENANT_ID", "other-tenant")
    assert repo.get_knowledge_base(kb["id"]) is None
    assert repo.list_knowledge_bases() == []
    assert repo.get_knowledge_document(doc["id"]) is None
    assert repo.list_knowledge_documents() == []
    assert repo.knowledge_stats() == {"documents": 0, "chunks": 0}
    assert repo.delete_knowledge_document(doc["id"]) is False
    assert repo.delete_knowledge_base(kb["id"]) is None

    monkeypatch.delenv("HERMES_TENANT_ID")
    assert repo.get_knowledge_document(doc["id"]) is not None
