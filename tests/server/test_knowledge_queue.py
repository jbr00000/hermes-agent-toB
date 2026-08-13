"""Stage-4 queue tests: runtime knowledge queue primitives + API-side enqueue.

本地 in-process runtime（无 Redis）路径 + 503/embedded 分支。
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from server.storage import (
    get_repository,
    get_runtime_store,
    init_storage,
    reset_storage_for_tests,
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("HERMES_DATABASE_URL", "HERMES_TENANT_ID", "HERMES_CUSTOMER_ID", "HERMES_REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER", raising=False)
    reset_storage_for_tests()
    init_storage()
    return get_runtime_store()


def _job(job_id: str = "job-1", doc_id: str = "doc-1") -> dict:
    return {"job_id": job_id, "doc_id": doc_id, "user_id": "admin-1", "queued_at": time.time()}


# ------------------------------------------------------- runtime 原语（本地）


def test_enqueue_claim_finish_cycle(store) -> None:
    assert store.enqueue_knowledge_job(_job()) is True
    assert store.enqueue_knowledge_job(_job()) is False  # 重复入队被拒绝

    claimed = store.claim_knowledge_job("worker-1", timeout_seconds=0)
    assert claimed["job_id"] == "job-1"
    assert claimed["delivery_attempt"] == 1

    # 非 owner 不能 finish；owner 可以
    assert store.finish_knowledge_job("job-1", "worker-2", status="succeeded") is False
    assert store.finish_knowledge_job("job-1", "worker-1", status="succeeded") is True
    assert store.knowledge_job_state("job-1")["state"] == "succeeded"


def test_heartbeat_extends_lease_only_for_owner(store) -> None:
    store.enqueue_knowledge_job(_job())
    store.claim_knowledge_job("worker-1", timeout_seconds=0, lease_seconds=1)

    assert store.heartbeat_knowledge_job("job-1", "worker-2") is False
    assert store.heartbeat_knowledge_job("job-1", "worker-1") is True
    state = store.knowledge_job_state("job-1")
    assert state["lease_until"] > time.time()


def test_expired_lease_recovered_and_requeued(store) -> None:
    store.enqueue_knowledge_job(_job())
    store.claim_knowledge_job("worker-1", timeout_seconds=0, lease_seconds=0)  # 立即过期

    expired = store.take_expired_knowledge_jobs(now=time.time() + 10)
    assert [j["job_id"] for j in expired] == ["job-1"]
    assert store.knowledge_job_state("job-1")["state"] == "stale"

    assert store.requeue_knowledge_job(_job()) is True
    reclaimed = store.claim_knowledge_job("worker-2", timeout_seconds=0)
    assert reclaimed["job_id"] == "job-1"
    assert reclaimed["delivery_attempt"] == 2  # attempt 递增


def test_claim_timeout_returns_none(store) -> None:
    assert store.claim_knowledge_job("worker-1", timeout_seconds=0) is None


# ------------------------------------------------------- API 侧 enqueue


def test_enqueue_api_503_without_redis_and_embedded(store, monkeypatch) -> None:
    from server.knowledge import queue

    monkeypatch.delenv("HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        queue.enqueue_knowledge_job(doc_id="doc-1", user_id="admin-1")
    assert excinfo.value.status_code == 503


def test_enqueue_api_embedded_worker_runs_pipeline(store, tmp_path, monkeypatch) -> None:
    from server.knowledge import queue

    monkeypatch.setenv("HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER", "1")
    repo = get_repository()
    files_dir = tmp_path / "knowledge" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    path = files_dir / "doc.md"
    path.write_text("# 标题\n\n正文内容，足够长到不会被合并掉。" * 10, encoding="utf-8")
    document = repo.create_knowledge_document(
        uploader_id="admin-1",
        title="文档",
        file_name="doc.md",
        file_ext=".md",
        size_bytes=path.stat().st_size,
        file_path=str(path),
    )

    job = queue.enqueue_knowledge_job(doc_id=document["id"], user_id="admin-1")
    assert job["status"] == "queued"

    # 内嵌 worker 会异步执行 pipeline；sync 阶段未配置 ES → 文档最终 failed，
    # 但状态一定离开了 pending/queued——轮询等待终态
    deadline = time.time() + 10
    while time.time() < deadline:
        current = repo.get_knowledge_document(document["id"])
        if current["status"] in {"ready", "failed"}:
            break
        time.sleep(0.1)
    assert current["status"] == "failed"  # sync 无 ES/embedding 配置 → failed（链路已被走到）
    job_row = repo.get_knowledge_job(job["id"])
    assert job_row["status"] == "failed"
    # chunk 已经落库（MySQL 事实源先行）
    assert len(repo.list_knowledge_chunks(document["id"])) >= 1
