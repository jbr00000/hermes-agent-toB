from __future__ import annotations

import time

from server.storage.runtime import RuntimeStore


def _job(request_id: str = "run-1", phase: str = "plan") -> dict:
    return {
        "request_id": request_id,
        "user_id": "user-1",
        "task_id": "task-1",
        "session_id": "session-1",
        "phase": phase,
        "message": "Create a plan",
    }


def test_local_agent_queue_is_idempotent_and_acknowledges_claims(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    store = RuntimeStore()

    assert store.enqueue_agent_job(_job()) is True
    assert store.enqueue_agent_job(_job()) is False

    claimed = store.claim_agent_job("worker-1", timeout_seconds=0, lease_seconds=10)
    assert claimed is not None
    assert claimed["request_id"] == "run-1"
    assert store.agent_job_state("run-1")["state"] == "processing"
    assert store.heartbeat_agent_job("run-1", "worker-1", lease_seconds=10) is True

    store.finish_agent_job("run-1", "worker-1", status="completed")
    assert store.agent_job_state("run-1")["state"] == "completed"
    assert store.claim_agent_job("worker-1", timeout_seconds=0) is None


def test_expired_agent_claim_is_returned_for_policy_recovery(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    store = RuntimeStore()
    store.enqueue_agent_job(_job(phase="execute"))
    assert store.claim_agent_job("worker-1", timeout_seconds=0, lease_seconds=1)

    stale = store.take_expired_agent_jobs(now=time.time() + 2)

    assert [job["request_id"] for job in stale] == ["run-1"]
    assert store.agent_job_state("run-1")["state"] == "stale"
    assert store.requeue_agent_job(stale[0]) is True
    assert store.claim_agent_job("worker-2", timeout_seconds=0)["request_id"] == "run-1"
    assert store.finish_agent_job("run-1", "worker-1", status="completed") is False
    assert store.agent_job_state("run-1")["worker_id"] == "worker-2"


def test_agent_event_buffer_and_snapshot_can_be_replayed(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    store = RuntimeStore()
    store.append_event("run-1", 1, "task.status", {"status": "queued"})
    store.append_event("run-1", 3, "tool.started", {"tool_name": "terminal"})
    store.save_chat_snapshot("run-1", "session-1", "partial", 2)

    assert [event["id"] for event in store.list_events("run-1", after_id=1)] == [3]
    assert store.last_event_id("run-1") == 3
    assert store.get_chat_snapshot("run-1")["content"] == "partial"


def test_worker_requeues_planning_but_does_not_replay_started_execution(
    monkeypatch, tmp_path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server.storage import get_repository, get_runtime_store, init_storage
    from server.storage import reset_storage_for_tests
    from server.worker import AgentWorker

    reset_storage_for_tests()
    init_storage()
    repository = get_repository()
    store = get_runtime_store()
    worker = AgentWorker("recovery-worker")

    plan_task = repository.create_agent_task("user-1")
    plan_job = {
        **_job("plan-run", "plan"),
        "task_id": plan_task["id"],
        "session_id": plan_task["session_id"],
    }
    repository.enqueue_task_run(
        "plan-run", "user-1", plan_task["id"], "plan", plan_job
    )
    store.enqueue_agent_job(plan_job)
    store.claim_agent_job("dead-worker", timeout_seconds=0, lease_seconds=1)
    repository.start_task_run("plan-run", "dead-worker")
    store.take_expired_agent_jobs(now=time.time() + 2)
    worker.recover()
    assert repository.get_task_run("plan-run")["status"] == "queued"
    assert store.agent_job_state("plan-run")["state"] == "queued"
    assert store.claim_agent_job("cleanup-worker", timeout_seconds=0)
    store.finish_agent_job("plan-run", "cleanup-worker", status="failed")
    repository.finish_model_run("plan-run", status="failed", error="test cleanup")
    repository.finish_task_run(
        "plan-run", status="failed", task_status="failed", error="test cleanup"
    )

    execute_task = repository.create_agent_task("user-1")
    execute_job = {
        **_job("execute-run", "execute"),
        "task_id": execute_task["id"],
        "session_id": execute_task["session_id"],
    }
    repository.enqueue_task_run(
        "execute-run", "user-1", execute_task["id"], "execute", execute_job
    )
    store.enqueue_agent_job(execute_job)
    store.claim_agent_job("dead-worker", timeout_seconds=0, lease_seconds=1)
    repository.start_task_run("execute-run", "dead-worker")
    store.take_expired_agent_jobs(now=time.time() + 2)
    worker.recover()
    assert repository.get_task_run("execute-run")["status"] == "failed"
    assert store.agent_job_state("execute-run")["state"] == "failed"


def test_worker_stops_requeueing_poison_job_at_max_attempts(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AGENT_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server.storage import get_repository, get_runtime_store, init_storage
    from server.storage import reset_storage_for_tests
    import server.worker as worker_module

    reset_storage_for_tests()
    init_storage()
    repository = get_repository()
    store = get_runtime_store()
    task = repository.create_agent_task("user-1")
    job = {
        **_job("poison-run", "plan"),
        "task_id": task["id"],
        "session_id": task["session_id"],
    }
    repository.enqueue_task_run("poison-run", "user-1", task["id"], "plan", job)
    store.enqueue_agent_job(job)

    def fail_before_start(_job_payload, _worker_id):
        raise RuntimeError("deterministic poison message")

    monkeypatch.setattr(worker_module, "execute_agent_job", fail_before_start)
    worker = worker_module.AgentWorker("poison-worker")

    assert worker.run_once(timeout_seconds=0) is True
    worker.recover()
    assert worker.run_once(timeout_seconds=0) is True
    worker.recover()

    assert repository.get_task_run("poison-run")["status"] == "failed"
    assert repository.get_owned_task("user-1", task["id"])["current_run_id"] is None
    assert store.agent_job_state("poison-run")["attempt"] == 2


def test_durable_cancel_survives_runtime_state_loss(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server.storage import get_repository, init_storage, reset_storage_for_tests
    import server.agent_execution as execution

    reset_storage_for_tests()
    init_storage()
    repository = get_repository()
    task = repository.create_agent_task("user-1")
    job = {
        **_job("cancel-run", "plan"),
        "task_id": task["id"],
        "session_id": task["session_id"],
        "queued_at": time.time(),
    }
    repository.enqueue_task_run("cancel-run", "user-1", task["id"], "plan", job)
    assert repository.request_task_run_cancel("user-1", "cancel-run") is True

    fresh_runtime = RuntimeStore()
    fresh_runtime.enqueue_agent_job(job)
    assert fresh_runtime.claim_agent_job("cancel-worker", timeout_seconds=0)
    assert fresh_runtime.is_cancelled("cancel-run") is False
    monkeypatch.setattr(execution, "get_repository", lambda: repository)
    monkeypatch.setattr(execution, "get_runtime_store", lambda: fresh_runtime)

    status = execution.execute_agent_job(job, "cancel-worker")

    assert status == "cancelled"
    assert repository.get_task_run("cancel-run")["status"] == "cancelled"


def test_mysql_recovery_scan_still_runs_when_redis_scan_fails(monkeypatch) -> None:
    import server.worker as worker_module

    worker = worker_module.AgentWorker("recovery-worker")
    mysql_scanned = False

    class Runtime:
        def take_expired_agent_jobs(self):
            raise RuntimeError("Redis unavailable")

    class Repository:
        def list_recoverable_task_runs(self):
            nonlocal mysql_scanned
            mysql_scanned = True
            return []

    worker.runtime_store = Runtime()
    worker.repository = Repository()
    worker.recover()

    assert mysql_scanned is True
