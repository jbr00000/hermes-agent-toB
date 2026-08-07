"""Background worker for durable Agent task execution."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid

from server.agent_execution import execute_agent_job, fail_interrupted_execute_job
from server.storage import get_repository, get_runtime_store

logger = logging.getLogger(__name__)


class AgentWorker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.runtime_store = get_runtime_store()
        self.repository = get_repository()
        self._last_recovery_at = 0.0

    def _recover_job(self, job: dict) -> None:
        request_id = str(job["request_id"])
        run = self.repository.get_task_run(request_id)
        if run is None or run["status"] not in {"queued", "running"}:
            self.runtime_store.finish_agent_job(
                request_id,
                self.worker_id,
                status=run["status"] if run else "missing",
                allow_stale=True,
            )
            return
        if run["status"] == "queued" or run["phase"] == "plan":
            self.repository.requeue_task_run(request_id)
            self.runtime_store.requeue_agent_job(job)
            return
        reason = "Agent execution Worker lease expired"
        fail_interrupted_execute_job(job, reason)
        from server.sandbox import release_task_sandbox

        release_task_sandbox(str(job["user_id"]), str(job["task_id"]))
        self.runtime_store.finish_agent_job(
            request_id, self.worker_id, status="failed", allow_stale=True
        )

    def recover(self) -> None:
        for job in self.runtime_store.take_expired_agent_jobs():
            self._recover_job(job)

        for run in self.repository.list_recoverable_task_runs():
            job = run.get("request_payload") or {}
            if not job or not job.get("request_id"):
                if run["status"] == "running":
                    logger.error("Cannot recover Agent run %s without payload", run["id"])
                continue
            state = self.runtime_store.agent_job_state(run["id"])
            if state and state.get("state") in {"queued", "processing"}:
                continue
            self._recover_job(job)

    def run_once(self, *, timeout_seconds: int = 2) -> bool:
        self.runtime_store.touch_worker(self.worker_id)
        now = time.monotonic()
        if now - self._last_recovery_at >= 5:
            self.recover()
            self._last_recovery_at = now
        job = self.runtime_store.claim_agent_job(
            self.worker_id,
            timeout_seconds=timeout_seconds,
        )
        if job is None:
            return False
        request_id = str(job["request_id"])
        status = "failed"
        try:
            status = execute_agent_job(job, self.worker_id)
        except Exception:
            logger.exception("Unhandled Agent worker failure for %s", request_id)
        finally:
            self.runtime_store.finish_agent_job(
                request_id,
                self.worker_id,
                status=status,
            )
        return True

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        logger.info("Agent worker %s started", self.worker_id)
        while not stop.is_set():
            try:
                self.run_once(timeout_seconds=2)
            except Exception:
                logger.exception("Agent worker loop failed")
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
    from server import audit, auth, features, memory

    auth.init_db()
    memory.init_db()
    audit.init_db()
    features.apply_terminal_backend()


def main() -> None:
    _bootstrap()
    AgentWorker().run_forever()


if __name__ == "__main__":
    main()
