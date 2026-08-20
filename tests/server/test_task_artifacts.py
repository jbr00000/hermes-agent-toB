from __future__ import annotations

from fastapi.testclient import TestClient


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

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_task(client: TestClient, headers: dict[str, str]) -> dict:
    created = client.post("/tasks", headers=headers, json={"title": "整理交付文件"})
    assert created.status_code == 201
    return created.json()["task"]


def _admin_user_id(client: TestClient, headers: dict[str, str]) -> str:
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    return me.json()["user"]["id"]


def test_task_artifacts_list_and_download(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = _create_task(client, headers)

    listed = client.get(f"/tasks/{task['id']}/artifacts", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["artifacts"] == []

    # 直接往任务工作区写文件，模拟 agent 在沙箱 cwd 里的产物
    from server.sandbox import task_workspace_dir

    workspace = task_workspace_dir(_admin_user_id(client, headers), task["id"])
    (workspace / "费用测算结果.xlsx").write_bytes(b"excel-bytes")
    (workspace / "子目录").mkdir()
    (workspace / "子目录" / "memo.txt").write_text("nested", encoding="utf-8")
    (workspace / ".hidden").write_text("secret", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("secret", encoding="utf-8")

    artifacts = client.get(
        f"/tasks/{task['id']}/artifacts", headers=headers
    ).json()["artifacts"]
    by_path = {item["path"]: item for item in artifacts}
    assert set(by_path) == {"费用测算结果.xlsx", "子目录/memo.txt"}
    assert by_path["费用测算结果.xlsx"]["size_bytes"] == len(b"excel-bytes")
    assert by_path["费用测算结果.xlsx"]["name"] == "费用测算结果.xlsx"

    downloaded = client.get(
        f"/tasks/{task['id']}/artifacts/download",
        headers=headers,
        params={"path": "子目录/memo.txt"},
    )
    assert downloaded.status_code == 200
    assert downloaded.text == "nested"

    # 越权：其他用户看不到产物
    created_user = client.post(
        "/users",
        headers=headers,
        json={"username": "other", "password": "password-123", "role": "user"},
    )
    assert created_user.status_code == 200
    other_headers = _login(client, "other", "password-123")
    changed = client.post(
        "/auth/change-password",
        headers=other_headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200, changed.text
    other_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert (
        client.get(f"/tasks/{task['id']}/artifacts", headers=other_headers).status_code
        == 404
    )
    assert (
        client.get(
            f"/tasks/{task['id']}/artifacts/download",
            headers=other_headers,
            params={"path": "费用测算结果.xlsx"},
        ).status_code
        == 404
    )


def test_task_artifact_download_rejects_escape_and_hidden(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = _create_task(client, headers)

    from server.sandbox import task_workspace_dir

    workspace = task_workspace_dir(_admin_user_id(client, headers), task["id"])
    (workspace / "visible.txt").write_text("ok", encoding="utf-8")
    (workspace / ".hidden.txt").write_text("secret", encoding="utf-8")

    for bad in ("../visible.txt", "../../etc/passwd", "/absolute/path", ".hidden.txt"):
        response = client.get(
            f"/tasks/{task['id']}/artifacts/download",
            headers=headers,
            params={"path": bad},
        )
        assert response.status_code == 400, bad

    missing = client.get(
        f"/tasks/{task['id']}/artifacts/download",
        headers=headers,
        params={"path": "nope.txt"},
    )
    assert missing.status_code == 404
