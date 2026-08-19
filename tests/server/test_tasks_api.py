from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    monkeypatch.setenv("HERMES_ALLOW_EMBEDDED_AGENT_WORKER", "1")

    from server import auth
    from server.storage import reset_storage_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_agent_task_is_user_scoped_and_defaults_to_read(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")

    created = client.post("/tasks", headers=admin_headers, json={"title": "检查费用测算"})
    assert created.status_code == 201
    task = created.json()["task"]
    assert task["status"] == "draft"
    assert task["permission"]["mode"] == "read"
    assert task["session"]["interaction_type"] == "agent"

    listed = client.get("/tasks", headers=admin_headers).json()["tasks"]
    assert [item["id"] for item in listed] == [task["id"]]
    assert client.post(
        f"/tasks/{task['id']}/execute",
        headers=admin_headers,
        json={"request_id": "execute-before-approval"},
    ).status_code == 409

    created_user = client.post(
        "/users",
        headers=admin_headers,
        json={"username": "other", "password": "password-123", "role": "user"},
    )
    assert created_user.status_code == 200
    other_headers = _login(client, "other", "password-123")
    # 新建用户带强制改密标记；改密清掉，否则白名单外端点一律 403。
    changed = client.post(
        "/auth/change-password",
        headers=other_headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200, changed.text
    other_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert client.get(f"/tasks/{task['id']}", headers=other_headers).status_code == 404

    deleted = client.delete(f"/tasks/{task['id']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.get("/tasks", headers=admin_headers).json()["tasks"] == []

    second = client.post("/tasks", headers=admin_headers, json={}).json()["task"]
    assert second["title"] == "新任务"
    deleted_via_session = client.delete(
        f"/sessions/{second['session_id']}", headers=admin_headers
    )
    assert deleted_via_session.status_code == 200
    assert client.get("/tasks", headers=admin_headers).json()["tasks"] == []
    assert client.get(f"/tasks/{second['id']}", headers=admin_headers).status_code == 404


def test_agent_plan_approval_execute_and_permission_persists(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = client.post("/tasks", headers=headers, json={}).json()["task"]
    captured: list[dict] = []

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db"]

        def __init__(self, callbacks: dict) -> None:
            self.callbacks = callbacks

        def chat(self, message, stream_callback=None, task_id=None):
            captured.append({"message": message, "task_id": task_id, **self.callbacks})
            if self.callbacks.get("tool_start_callback"):
                self.callbacks["tool_start_callback"](
                    "tool-1",
                    "db_query",
                    {
                        "query": "SELECT 1",
                        "password": "secret",
                        "access_token": "token-value",
                        "client_secret": "secret-value",
                    },
                )
            if stream_callback:
                stream_callback("执行计划正文")
            if self.callbacks.get("tool_complete_callback"):
                self.callbacks["tool_complete_callback"](
                    "tool-1",
                    "db_query",
                    {
                        "query": "SELECT 1",
                        "password": "secret",
                        "access_token": "token-value",
                        "client_secret": "secret-value",
                    },
                    "1",
                )
            return "执行计划正文"

        def interrupt(self, message=None):
            return None

    import server.agent_factory as agent_factory

    def build_agent(**kwargs):
        return FakeAgent(kwargs)

    monkeypatch.setattr(agent_factory, "build_agent", build_agent)

    plan = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "先分析任务并生成计划", "request_id": "plan-request"},
    )
    assert plan.status_code == 200
    assert "event: plan.required" in plan.text
    assert captured[-1]["mode"] == "plan"
    assert captured[-1]["permission_mode"] == "read"
    assert captured[-1]["task_id"].startswith("tob-")

    detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert detail["status"] == "awaiting_approval"
    assert detail["plan"]["content"] == "执行计划正文"
    assert detail["plan"]["status"] == "pending"
    assert {event["event_type"] for event in detail["events"]} >= {
        "tool.started",
        "tool.completed",
    }
    assert all(
        event["payload"]["arguments"]["keys"]
        == ["access_token", "client_secret", "password", "query"]
        for event in detail["events"]
    )
    assert all(
        "'password': 'secret'" not in str(event["payload"])
        and "token-value" not in str(event["payload"])
        and "secret-value" not in str(event["payload"])
        and "SELECT 1" not in str(event["payload"])
        for event in detail["events"]
    )
    assert all(event["risk_level"] == "read" for event in detail["events"])

    approved = client.post(f"/tasks/{task['id']}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["task"]["status"] == "ready"

    permission = client.put(
        f"/tasks/{task['id']}/permission",
        headers=headers,
        # 不传 ttl_seconds：持久权限，expires_at 为 NULL
        json={"mode": "full"},
    )
    assert permission.status_code == 200
    assert permission.json()["permission"]["mode"] == "full"
    assert permission.json()["permission"]["expires_at"] is None

    execute = client.post(
        f"/tasks/{task['id']}/execute",
        headers=headers,
        json={"request_id": "execute-request"},
    )
    assert execute.status_code == 200
    assert "event: task.status" in execute.text
    assert captured[-1]["mode"] == "execute"
    assert captured[-1]["permission_mode"] == "full"
    assert "执行计划正文" in captured[-1]["message"]

    completed = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert completed["status"] == "completed"
    # 权限一次切换持久化：执行结束后仍是 full，不再自动回落只读
    assert completed["permission"]["mode"] == "full"
    assert [run["phase"] for run in completed["runs"]] == ["plan", "execute"]
    assert completed["messages"][-2]["content"] == "执行已批准计划"

    retried = client.post(
        f"/tasks/{task['id']}/retry",
        headers=headers,
        json={"request_id": "execute-retry"},
    )
    assert retried.status_code == 200
    after_retry = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert [run["phase"] for run in after_retry["runs"]] == ["plan", "execute", "execute"]
    assert after_retry["runs"][-1]["attempt"] == 2
    assert captured[-1]["permission_mode"] == "full"

    deleted = client.delete(f"/tasks/{task['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/tasks/{task['id']}", headers=headers).status_code == 404


def test_execute_with_failed_tool_marks_task_failed(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = client.post("/tasks", headers=headers, json={}).json()["task"]

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "terminal"]

        def __init__(self, callbacks: dict) -> None:
            self.callbacks = callbacks

        def chat(self, message, stream_callback=None, task_id=None):
            if self.callbacks["mode"] == "execute":
                self.callbacks["tool_start_callback"]("tool-1", "terminal", {})
                self.callbacks["tool_complete_callback"](
                    "tool-1",
                    "terminal",
                    {},
                    '{"status":"error","error":"sandbox unavailable"}',
                )
                return "The sandbox command could not be executed."
            return "1. Run the command in the sandbox."

        def interrupt(self, message=None):
            return None

    import server.agent_factory as agent_factory

    monkeypatch.setattr(
        agent_factory,
        "build_agent",
        lambda **kwargs: FakeAgent(kwargs),
    )

    assert client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "plan the sandbox command"},
    ).status_code == 200
    assert client.post(f"/tasks/{task['id']}/approve", headers=headers).status_code == 200
    assert client.put(
        f"/tasks/{task['id']}/permission",
        headers=headers,
        json={"mode": "full", "ttl_seconds": 600},
    ).status_code == 200

    response = client.post(
        f"/tasks/{task['id']}/execute",
        headers=headers,
        json={},
    )
    assert response.status_code == 200
    assert '"status": "failed"' in response.text
    detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert detail["status"] == "failed"
    # 工具失败也不吊销权限：持久化切换，重试仍在 full 下运行
    assert detail["permission"]["mode"] == "full"
    assert detail["events"][-1]["status"] == "failed"


def test_agent_interruption_is_persisted_as_cancelled(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = client.post("/tasks", headers=headers, json={}).json()["task"]
    started = threading.Event()
    interrupted = threading.Event()

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db"]

        def chat(self, message, stream_callback=None, task_id=None):
            started.set()
            interrupted.wait(timeout=5)
            raise RuntimeError("interrupted")

        def interrupt(self, message=None):
            interrupted.set()

    import server.agent_factory as agent_factory

    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: FakeAgent())

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post,
            f"/tasks/{task['id']}/plan",
            headers=headers,
            json={"message": "plan a long task", "request_id": "cancelled-plan"},
        )
        assert started.wait(timeout=5)
        for _ in range(50):
            detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
            if detail["current_run_id"] == "cancelled-plan":
                break
            time.sleep(0.02)
        cancelled = client.post(f"/tasks/{task['id']}/cancel", headers=headers)
        assert cancelled.status_code == 202
        response = pending.result(timeout=5)

    assert response.status_code == 200
    assert '"status": "cancelled"' in response.text
    detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert detail["status"] == "cancelled"
    assert detail["permission"]["mode"] == "read"
    assert detail["runs"][-1]["status"] == "cancelled"


def test_create_agent_task_can_snapshot_owned_chat_context(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    source = client.post(
        "/sessions",
        headers=headers,
        json={"interaction_type": "chat", "title": "费用测算讨论"},
    ).json()["session"]

    from server.storage import get_repository

    repository = get_repository()
    repository.append_message(source["id"], "user", "请分析费用测算文档")
    repository.append_message(source["id"], "assistant", "需要进一步读取原始文件")

    created = client.post(
        "/tasks",
        headers=headers,
        json={"source_session_id": source["id"]},
    )

    assert created.status_code == 201
    task = created.json()["task"]
    assert task["source_session_id"] == source["id"]
    assert task["title"] == "费用测算讨论"
    assert [message["content"] for message in task["messages"]] == [
        "请分析费用测算文档",
        "需要进一步读取原始文件",
    ]


def test_agent_queue_failure_returns_503_and_releases_task(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = client.post("/tasks", headers=headers, json={}).json()["task"]

    from server.storage import get_runtime_store

    runtime_store = get_runtime_store()

    def unavailable(_job):
        raise RuntimeError("Redis Agent queue is unavailable")

    monkeypatch.setattr(runtime_store, "enqueue_agent_job", unavailable)
    response = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "生成执行计划", "request_id": "queue-unavailable"},
    )

    assert response.status_code == 503
    detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert detail["current_run_id"] is None
    assert detail["runs"][-1]["status"] == "failed"
