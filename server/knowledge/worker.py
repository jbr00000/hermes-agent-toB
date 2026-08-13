"""Knowledge worker: consumes knowledge jobs and runs the build pipeline.

``python -m server.knowledge.worker``（compose 里为 hermes-knowledge-worker
容器）。心跳租约过期 / 进程崩溃的 job 由 recover() 重新入队——pipeline 幂等
（chunk 原子替换 + ES/Milvus delete-then-write），重跑安全。
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from typing import Any

from server.storage import get_repository, get_runtime_store

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_STALE_HEARTBEAT_SECONDS = 180  # DB 里 running 但心跳停更超过此值 → 回收


class KnowledgeWorker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or (
            f"kb-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.runtime_store = get_runtime_store()
        self.repository = get_repository()
        self._last_recovery_at = 0.0

    # -------------------------------------------------------------- recovery

    def _fail_job(self, job: dict[str, Any], reason: str) -> None:
        job_id = str(job["job_id"])
        self.repository.update_knowledge_job(
            job_id, status="failed", error=reason, finished_at=time.time()
        )
        self.repository.update_knowledge_document(
            str(job["doc_id"]), status="failed", error=reason
        )
        self.runtime_store.finish_knowledge_job(
            job_id, self.worker_id, status="failed", allow_stale=True
        )

    def _recover_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        row = self.repository.get_knowledge_job(job_id)
        if row is None or row["status"] not in {"queued", "running"}:
            self.runtime_store.finish_knowledge_job(
                job_id,
                self.worker_id,
                status=row["status"] if row else "missing",
                allow_stale=True,
            )
            return
        if int(row.get("attempt") or 0) >= _MAX_ATTEMPTS:
            self._fail_job(job, f"知识库任务超过 {_MAX_ATTEMPTS} 次投递仍失败")
            return
        # 幂等 pipeline → 直接重新入队
        self.repository.update_knowledge_job(job_id, status="queued", worker_id="")
        self.repository.update_knowledge_document(str(job["doc_id"]), status="pending")
        self.runtime_store.requeue_knowledge_job(job)

    def recover(self) -> None:
        try:
            expired_jobs = self.runtime_store.take_expired_knowledge_jobs()
        except RuntimeError:
            expired_jobs = []
            logger.warning("Redis knowledge recovery scan unavailable", exc_info=True)
        for job in expired_jobs:
            try:
                self._recover_job(job)
            except Exception:
                logger.exception("Could not recover knowledge job %s", job.get("job_id"))

        try:
            stale_rows = self.repository.list_stale_running_knowledge_jobs(
                time.time() - _STALE_HEARTBEAT_SECONDS
            )
        except Exception:
            logger.warning("DB knowledge recovery scan unavailable", exc_info=True)
            return
        for row in stale_rows:
            state = self.runtime_store.knowledge_job_state(row["id"])
            if state and state.get("state") in {"queued", "processing", "unavailable"}:
                continue
            job = {"job_id": row["id"], "doc_id": row["doc_id"], "user_id": row["user_id"]}
            try:
                self._recover_job(job)
            except Exception:
                logger.exception("Could not recover knowledge job %s", row["id"])

    # ----------------------------------------------------------------- loop

    def run_once(self, *, timeout_seconds: int = 2) -> bool:
        self.runtime_store.touch_worker(self.worker_id)
        now = time.monotonic()
        if now - self._last_recovery_at >= 5:
            self.recover()
            self._last_recovery_at = now
        job = self.runtime_store.claim_knowledge_job(
            self.worker_id, timeout_seconds=timeout_seconds
        )
        if job is None:
            return False
        job_id = str(job["job_id"])
        if int(job.get("delivery_attempt") or 0) > _MAX_ATTEMPTS:
            self._fail_job(job, f"知识库任务超过 {_MAX_ATTEMPTS} 次投递仍失败")
            return True
        status = "failed"
        try:
            from server.knowledge.pipeline import run_job

            status = run_job(job, self.worker_id)
        except Exception:
            logger.exception("Unhandled knowledge worker failure for %s", job_id)
        finally:
            self.runtime_store.finish_knowledge_job(
                job_id, self.worker_id, status=status
            )
        return True

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        logger.info("Knowledge worker %s started", self.worker_id)
        while not stop.is_set():
            try:
                self.run_once(timeout_seconds=2)
            except Exception:
                logger.exception("Knowledge worker loop failed")
                stop.wait(2)


def _bootstrap() -> None:
    home = os.environ.get("HERMES_HOME")
    if not home:
        raise SystemExit("HERMES_HOME must be set")
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(home, ".env"))
    except Exception:
        pass
    os.environ.setdefault("HERMES_HEADLESS", "1")
    from server.storage import init_storage

    init_storage()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _bootstrap()
    KnowledgeWorker().run_forever()


if __name__ == "__main__":
    main()
