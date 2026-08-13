"""Storage-layer tests for the enterprise knowledge base tables.

MySQL 是事实源：documents 走状态机（pending→parsing→syncing→ready/failed），
chunks 可整体替换（幂等重解析），jobs 支撑 worker 恢复。ES/Milvus 不在这里测。
"""
from __future__ import annotations

import time

import pytest


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


def _create_doc(repo, **overrides):
    values = {
        "uploader_id": "u-admin",
        "title": "员工手册",
        "file_name": "员工手册.pdf",
        "file_ext": ".pdf",
        "size_bytes": 1024,
        "file_path": "knowledge/files/doc-1.pdf",
    }
    values.update(overrides)
    return repo.create_knowledge_document(**values)


def test_document_lifecycle_and_stats(repo) -> None:
    doc = _create_doc(repo)
    assert doc["status"] == "pending"
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

    listed = repo.list_knowledge_documents()
    assert [d["id"] for d in listed] == [doc["id"]]
    assert repo.list_knowledge_documents(status="failed") == []
    assert repo.get_knowledge_document("missing") is None
    assert repo.update_knowledge_document("missing", status="ready") is None


def test_replace_knowledge_chunks_is_idempotent(repo) -> None:
    doc = _create_doc(repo)
    first = [
        {"content": "第一段", "doc_pos": 0, "chunk_title": "总则", "token_num": 10},
        {"content": "第二段", "doc_pos": 1, "chunk_title": "总则", "token_num": 12},
    ]
    assert repo.replace_knowledge_chunks(doc["id"], doc["file_name"], first) == 2
    ids_v1 = [c["id"] for c in repo.list_knowledge_chunks(doc["id"])]
    assert len(ids_v1) == 2

    # 重解析：整体替换，旧 chunk 不残留
    second = [{"content": "新内容", "doc_pos": 0, "chunk_title": "总则", "token_num": 8}]
    assert repo.replace_knowledge_chunks(doc["id"], doc["file_name"], second) == 1
    chunks = repo.list_knowledge_chunks(doc["id"])
    assert [c["content"] for c in chunks] == ["新内容"]
    assert chunks[0]["id"] not in ids_v1
    assert chunks[0]["doc_name"] == doc["file_name"]


def test_delete_document_cascades_chunks_and_jobs(repo) -> None:
    doc = _create_doc(repo)
    repo.replace_knowledge_chunks(
        doc["id"], doc["file_name"], [{"content": "x", "doc_pos": 0, "token_num": 1}]
    )
    job = repo.create_knowledge_job(doc_id=doc["id"], user_id="u-admin")

    assert repo.delete_knowledge_document(doc["id"]) is True

    assert repo.get_knowledge_document(doc["id"]) is None
    assert repo.list_knowledge_chunks(doc["id"]) == []
    assert repo.get_knowledge_job(job["id"]) is None
    assert repo.delete_knowledge_document(doc["id"]) is False


def test_knowledge_job_state_and_stale_recovery(repo) -> None:
    doc = _create_doc(repo)
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


def test_documents_are_tenant_scoped(repo, monkeypatch) -> None:
    doc = _create_doc(repo)

    monkeypatch.setenv("HERMES_TENANT_ID", "other-tenant")
    assert repo.get_knowledge_document(doc["id"]) is None
    assert repo.list_knowledge_documents() == []
    assert repo.knowledge_stats() == {"documents": 0, "chunks": 0}
    assert repo.delete_knowledge_document(doc["id"]) is False

    monkeypatch.delenv("HERMES_TENANT_ID")
    assert repo.get_knowledge_document(doc["id"]) is not None
