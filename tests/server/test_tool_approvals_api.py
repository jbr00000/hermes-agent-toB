"""controlled 档审批路由（/tasks/{id}/tool-approvals）的端到端测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server.storage import get_repository
from server.storage.runtime import get_runtime_store, reset_runtime_store_for_tests


def _client(monkeypatch, tmp_path) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)

    from server import auth
    from server.storage import reset_storage_for_tests

    auth._JWT_SECRET = None
    reset_storage_for_tests()
    reset_runtime_store_for_tests()

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _setup(monkeypatch, tmp_path) -> tuple[TestClient, dict, dict, dict]:
    """client + admin headers + 普通用户 row + 该用户的 agent task。"""
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    created = client.post(
        "/users",
        headers=admin_headers,
        json={"username": "worker", "password": "password-123", "role": "user"},
    )
    assert created.status_code == 200
    user = created.json()["user"]
    user_headers = _login(client, "worker", "password-123")
    # 新建用户带强制改密标记；改密清掉，否则白名单外端点一律 403。
    changed = client.post(
        "/auth/change-password",
        headers=user_headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200, changed.text
    user_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    task_resp = client.post("/tasks", headers=user_headers, json={"title": "受控任务"})
    assert task_resp.status_code == 201, task_resp.text
    return client, user_headers, user, task_resp.json()["task"]


def _add_approval(user: dict, task: dict, **overrides) -> dict:
    kwargs = {
        "task_id": task["id"],
        "run_request_id": "run-1",
        "user_id": user["id"],
        "tool_name": "terminal",
        "command_preview": "cat /etc/hosts",
        "args_fingerprint": "0123456789abcdef",
    }
    kwargs.update(overrides)
    return get_repository().create_tool_approval(**kwargs)


def test_list_filters_by_status(monkeypatch, tmp_path) -> None:
    client, headers, user, task = _setup(monkeypatch, tmp_path)
    pending = _add_approval(user, task, command_preview="ls")
    decided = _add_approval(user, task, command_preview="pwd")
    get_repository().decide_tool_approval(
        decided["id"], user_id=user["id"], decision="approved"
    )

    all_rows = client.get(f"/tasks/{task['id']}/tool-approvals", headers=headers)
    assert all_rows.status_code == 200
    assert {row["id"] for row in all_rows.json()["approvals"]} == {
        pending["id"],
        decided["id"],
    }

    pending_only = client.get(
        f"/tasks/{task['id']}/tool-approvals?status=pending", headers=headers
    )
    assert [row["id"] for row in pending_only.json()["approvals"]] == [pending["id"]]


def test_decide_allow_and_double_decide_conflict(monkeypatch, tmp_path) -> None:
    client, headers, user, task = _setup(monkeypatch, tmp_path)
    approval = _add_approval(user, task)

    first = client.post(
        f"/tasks/{task['id']}/tool-approvals/{approval['id']}",
        headers=headers,
        json={"decision": "allow"},
    )
    assert first.status_code == 200
    assert first.json()["approval"]["status"] == "approved"
    assert first.json()["approval"]["decided_by"] == user["id"]

    second = client.post(
        f"/tasks/{task['id']}/tool-approvals/{approval['id']}",
        headers=headers,
        json={"decision": "deny"},
    )
    assert second.status_code == 409


def test_allow_all_sets_run_flag(monkeypatch, tmp_path) -> None:
    client, headers, user, task = _setup(monkeypatch, tmp_path)
    approval = _add_approval(user, task, run_request_id="run-allow-all")

    response = client.post(
        f"/tasks/{task['id']}/tool-approvals/{approval['id']}",
        headers=headers,
        json={"decision": "allow_all"},
    )
    assert response.status_code == 200
    assert response.json()["approval"]["status"] == "approved"
    assert get_runtime_store().is_run_flag("run-allow-all", "allow_all") is True


def test_other_user_gets_404(monkeypatch, tmp_path) -> None:
    client, headers, user, task = _setup(monkeypatch, tmp_path)
    approval = _add_approval(user, task)

    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    # 他人（包括 admin）看不到也决定不了这条审批。
    listed = client.get(f"/tasks/{task['id']}/tool-approvals", headers=admin_headers)
    assert listed.status_code == 404
    decided = client.post(
        f"/tasks/{task['id']}/tool-approvals/{approval['id']}",
        headers=admin_headers,
        json={"decision": "allow"},
    )
    assert decided.status_code == 404


def test_unknown_task_and_approval_404(monkeypatch, tmp_path) -> None:
    client, headers, user, task = _setup(monkeypatch, tmp_path)
    assert (
        client.get("/tasks/nope/tool-approvals", headers=headers).status_code == 404
    )
    assert (
        client.post(
            f"/tasks/{task['id']}/tool-approvals/nope",
            headers=headers,
            json={"decision": "allow"},
        ).status_code
        == 404
    )
