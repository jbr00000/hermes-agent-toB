"""API-side knowledge job enqueueing (mirrors server/agent_queue.py).

无 Redis 部署下，仅当 ``HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER=1`` 时用内嵌线程
兜底执行（开发/演示用）；否则 503——生产部署应起 ``hermes-knowledge-worker``
容器 + Redis。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from fastapi import HTTPException

from server.storage import get_repository, get_runtime_store

logger = logging.getLogger(__name__)

_embedded_jobs: set[str] = set()
_embedded_jobs_lock = threading.Lock()


def _embedded_worker_allowed() -> bool:
    return os.environ.get("HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER", "0") == "1"


def enqueue_knowledge_job(*, doc_id: str, user_id: str) -> dict[str, Any]:
    """Create the job row and push it onto the knowledge queue. Returns the job."""
    repository = get_repository()
    runtime_store = get_runtime_store()
    if not runtime_store.redis_enabled and not _embedded_worker_allowed():
        raise HTTPException(
            status_code=503,
            detail="知识库任务队列需要 Redis 与专用 Worker（或显式开启内嵌 Worker）",
        )
    job_row = repository.create_knowledge_job(doc_id=doc_id, user_id=user_id)
    job = {
        "job_id": job_row["id"],
        "doc_id": doc_id,
        "user_id": user_id,
        "queued_at": time.time(),
    }
    try:
        created = runtime_store.enqueue_knowledge_job(job)
    except RuntimeError as exc:
        repository.update_knowledge_job(
            job_row["id"], status="failed", error="知识库队列不可用", finished_at=time.time()
        )
        raise HTTPException(
            status_code=503,
            detail="知识库队列暂不可用",
            headers={"Retry-After": "5"},
        ) from exc
    if created:
        _start_embedded_worker(job_row["id"])
    return {**job_row, "payload": {**job_row.get("payload", {}), **job}}


def _start_embedded_worker(job_id: str) -> None:
    runtime_store = get_runtime_store()
    if runtime_store.redis_enabled or not _embedded_worker_allowed():
        return
    with _embedded_jobs_lock:
        if job_id in _embedded_jobs:
            return
        _embedded_jobs.add(job_id)

    def run() -> None:
        try:
            from server.knowledge.worker import KnowledgeWorker

            KnowledgeWorker(worker_id=f"embedded-kb-{job_id[:12]}").run_once(
                timeout_seconds=0
            )
        finally:
            with _embedded_jobs_lock:
                _embedded_jobs.discard(job_id)

    threading.Thread(
        target=run,
        daemon=True,
        name=f"embedded-knowledge-{job_id[:8]}",
    ).start()
