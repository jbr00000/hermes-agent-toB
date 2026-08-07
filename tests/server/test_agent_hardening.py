from __future__ import annotations

import asyncio
import json
import threading

from redis.exceptions import RedisError

from server.storage.runtime import RuntimeStore
from server.tool_events import sanitize_tool_event_payload, tool_risk_level


def test_tool_event_payload_contains_summaries_only() -> None:
    payload = sanitize_tool_event_payload(
        "tool.completed",
        "db_query",
        {
            "tool_call_id": "call-1",
            "arguments": {
                "sql": "SELECT password FROM users",
                "password": "plain-secret",
            },
            "result": {"rows": [{"password": "database-secret"}]},
            "duration_ms": 42,
        },
    )

    rendered = str(payload)
    assert payload["arguments"]["keys"] == ["password", "sql"]
    assert payload["arguments"]["sql_fingerprint"]
    assert payload["result"]["kind"] == "object"
    assert payload["duration_ms"] == 42
    assert "SELECT password" not in rendered
    assert "plain-secret" not in rendered
    assert "database-secret" not in rendered
    assert tool_risk_level("db_query") == "read"
    assert tool_risk_level("terminal") == "high_risk"


def test_redis_heartbeat_failure_is_unavailable_not_lost(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    store = RuntimeStore()

    class BrokenRedis:
        def eval(self, *_args, **_kwargs):
            raise RedisError("redis unavailable")

    store._redis = BrokenRedis()
    assert store.heartbeat_agent_job("run-1", "worker-1") is None


def test_redis_event_replay_uses_server_side_cursor(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    store = RuntimeStore()
    calls: list[tuple[str, str, str]] = []

    class CursorRedis:
        def zrangebyscore(self, key, minimum, maximum):
            calls.append((key, minimum, maximum))
            return [
                json.dumps(
                    {"id": 3, "event": "task.status", "data": {"status": "running"}}
                )
            ]

    store._redis = CursorRedis()
    events = store.list_events("run-1", after_id=2)

    assert [event["id"] for event in events] == [3]
    assert calls[0][1:] == ("(2", "+inf")


def test_stream_runtime_reads_are_offloaded_from_event_loop(monkeypatch) -> None:
    import server.agent_queue as agent_queue

    event_loop_thread = threading.get_ident()
    blocking_threads: list[int] = []

    class Repository:
        calls = 0

        def get_owned_model_run(self, user_id, request_id):
            return {"id": request_id, "user_id": user_id}

        def get_task_run(self, request_id):
            self.calls += 1
            if self.calls > 1:
                blocking_threads.append(threading.get_ident())
            return {
                "id": request_id,
                "user_id": "user-1",
                "task_id": "task-1",
                "status": "completed",
            }

    class Store:
        def list_events(self, request_id, *, after_id=0):
            blocking_threads.append(threading.get_ident())
            return [{"id": 1, "event": "final", "data": {"status": "completed"}}]

        def get_chat_snapshot(self, request_id):
            blocking_threads.append(threading.get_ident())
            return {"content": "done", "sequence": 1, "status": "completed"}

    class Request:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(agent_queue, "get_repository", lambda: Repository())
    monkeypatch.setattr(agent_queue, "get_runtime_store", lambda: Store())
    response = agent_queue.stream_agent_run(
        Request(), user_id="user-1", task_id="task-1", request_id="run-1"
    )

    async def consume() -> None:
        async for event in response.body_iterator:
            if event.get("event") == "done":
                break

    asyncio.run(consume())
    assert blocking_threads
    assert all(thread_id != event_loop_thread for thread_id in blocking_threads)
