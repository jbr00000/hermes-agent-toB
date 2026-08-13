"""Stage-5 API tests: /knowledge routes — 权限、启停开关、上传/删除/重试。

内嵌 Worker 模式（HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER=1）下上传会异步跑
pipeline；sync 阶段无 ES 配置会 failed——测试只断言到 202 与状态机离开 pending。
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, knowledge_enabled: bool = True) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    if knowledge_enabled:
        (home / "deployment.yaml").write_text(
            "knowledge:\n  enabled: true\n  max_file_mb: 1\n", encoding="utf-8"
        )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.setenv("HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER", "1")
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


def _admin_and_user(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    admin = _login(client, "admin", "correct-horse-battery-staple")
    client.post(
        "/users", headers=admin, json={"username": "regular", "password": "password-123"}
    )
    return admin, _login(client, "regular", "password-123")


_MD = "# 标题\n\n正文内容，足够长以避免被尾块合并规则吞掉。" * 10


def _upload(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/knowledge/documents",
        headers=headers,
        files={"file": ("规范.md", _MD.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_knowledge_disabled_returns_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=False)
    admin, _ = _admin_and_user(client)
    assert client.get("/knowledge/documents", headers=admin).status_code == 404
    response = client.post(
        "/knowledge/documents",
        headers=admin,
        files={"file": ("a.md", b"# x", "text/markdown")},
    )
    assert response.status_code == 404


def test_read_endpoints_open_to_regular_user(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)

    assert client.get("/knowledge/documents").status_code == 401
    response = client.get("/knowledge/documents", headers=user)
    assert response.status_code == 200
    assert response.json()["documents"] == []
    assert response.json()["stats"] == {"documents": 0, "chunks": 0}

    # 普通用户上传/删除/重试 → 403
    assert (
        client.post(
            "/knowledge/documents",
            headers=user,
            files={"file": ("a.md", b"# x", "text/markdown")},
        ).status_code
        == 403
    )
    assert client.delete("/knowledge/documents/doc-x", headers=user).status_code == 403
    assert client.post("/knowledge/documents/doc-x/retry", headers=user).status_code == 403


def test_upload_validates_extension_and_size(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, _ = _admin_and_user(client)

    response = client.post(
        "/knowledge/documents",
        headers=admin,
        files={"file": ("page.html", b"<p>x</p>", "text/html")},
    )
    assert response.status_code == 400
    assert "不支持的文件格式" in response.json()["detail"]

    big = b"x" * (2 * 1024 * 1024)  # 限制 1MB
    response = client.post(
        "/knowledge/documents",
        headers=admin,
        files={"file": ("big.md", big, "text/markdown")},
    )
    assert response.status_code == 413


def test_upload_then_poll_status_and_delete(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)

    payload = _upload(client, admin)
    document = payload["document"]
    assert document["status"] == "pending"
    assert document["file_name"] == "规范.md"
    assert payload["job_id"]

    # 内嵌 worker 异步跑：sync 无 ES → failed；断言状态机离开 pending 且文件保留
    deadline = time.time() + 10
    current = document
    while time.time() < deadline:
        current = client.get(
            f"/knowledge/documents/{document['id']}", headers=user
        ).json()["document"]
        if current["status"] in {"ready", "failed"}:
            break
        time.sleep(0.1)
    assert current["status"] == "failed"
    assert current["error"]

    # chunk 已落库（事实源先行），普通用户可读
    chunks = client.get(
        f"/knowledge/documents/{document['id']}/chunks", headers=user
    ).json()["chunks"]
    assert len(chunks) >= 1

    # failed → 可重试（202，retry_count 递增，状态回 pending）
    retry = client.post(f"/knowledge/documents/{document['id']}/retry", headers=admin)
    assert retry.status_code == 202
    retried = client.get(
        f"/knowledge/documents/{document['id']}", headers=admin
    ).json()["document"]
    assert retried["retry_count"] == 1

    # 删除：DB 记录消失，磁盘文件清理
    from hermes_constants import get_hermes_home

    file_path = get_hermes_home() / document["file_path"]
    assert file_path.exists()
    assert client.delete(f"/knowledge/documents/{document['id']}", headers=admin).status_code == 200
    assert client.get(f"/knowledge/documents/{document['id']}", headers=admin).status_code == 404
    assert not file_path.exists()


def test_retry_rejects_active_document(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, _ = _admin_and_user(client)

    payload = _upload(client, admin)
    doc_id = payload["document"]["id"]
    # 文档处于 pending/processing 中 → 409（除非 worker 刚好跑完变 failed）
    response = client.post(f"/knowledge/documents/{doc_id}/retry", headers=admin)
    assert response.status_code in {202, 409}
    if response.status_code == 409:
        assert "不能重试" in response.json()["detail"]
