"""API tests: /knowledge routes — 三步流程（建库→上传→选择解析）、权限、启停开关。

内嵌 Worker 模式（HERMES_ALLOW_EMBEDDED_KNOWLEDGE_WORKER=1）下 parse 会异步跑
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


def _create_base(client: TestClient, headers: dict[str, str], name: str = "规范库") -> dict:
    response = client.post("/knowledge/bases", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["base"]


def _upload(client: TestClient, headers: dict[str, str], kb_id: str) -> dict:
    response = client.post(
        f"/knowledge/bases/{kb_id}/documents",
        headers=headers,
        files={"file": ("规范.md", _MD.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_knowledge_disabled_returns_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=False)
    admin, _ = _admin_and_user(client)
    assert client.get("/knowledge/documents", headers=admin).status_code == 404
    assert client.get("/knowledge/bases", headers=admin).status_code == 404
    response = client.post("/knowledge/bases", headers=admin, json={"name": "x"})
    assert response.status_code == 404


def test_read_endpoints_open_to_regular_user(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)

    assert client.get("/knowledge/documents").status_code == 401
    assert client.get("/knowledge/bases").status_code == 401
    response = client.get("/knowledge/documents", headers=user)
    assert response.status_code == 200
    assert response.json()["documents"] == []
    assert response.json()["stats"] == {"documents": 0, "chunks": 0}
    assert client.get("/knowledge/bases", headers=user).status_code == 200

    # 普通用户所有变更操作 → 403
    assert client.post("/knowledge/bases", headers=user, json={"name": "x"}).status_code == 403
    assert (
        client.post(
            "/knowledge/bases/kb-x/documents",
            headers=user,
            files={"file": ("a.md", b"# x", "text/markdown")},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/knowledge/documents/parse", headers=user, json={"document_ids": ["d"]}
        ).status_code
        == 403
    )
    assert client.delete("/knowledge/bases/kb-x", headers=user).status_code == 403
    assert client.delete("/knowledge/documents/doc-x", headers=user).status_code == 403
    assert client.post("/knowledge/documents/doc-x/retry", headers=user).status_code == 403


def test_base_crud(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)

    base = _create_base(client, admin, "运维规范")
    assert base["doc_count"] == 0
    assert base["chunk_count"] == 0

    # 重名 → 409
    assert (
        client.post("/knowledge/bases", headers=admin, json={"name": "运维规范"}).status_code
        == 409
    )

    # 普通用户可见库列表（只读）
    bases = client.get("/knowledge/bases", headers=user).json()["bases"]
    assert [b["id"] for b in bases] == [base["id"]]

    # 改名 / 改描述
    renamed = client.patch(
        f"/knowledge/bases/{base['id']}",
        headers=admin,
        json={"name": "运维规范V2", "description": "运维类文档"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["base"]["name"] == "运维规范V2"

    # 改名撞已有名 → 409
    other = _create_base(client, admin, "制度库")
    conflict = client.patch(
        f"/knowledge/bases/{other['id']}", headers=admin, json={"name": "运维规范V2"}
    )
    assert conflict.status_code == 409

    assert client.delete("/knowledge/bases/missing", headers=admin).status_code == 404


def test_upload_stays_uploaded_until_parse(monkeypatch, tmp_path) -> None:
    """关键行为变更：上传不再自动解析。"""
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)
    base = _create_base(client, admin)

    payload = _upload(client, admin, base["id"])
    document = payload["document"]
    assert document["status"] == "uploaded"
    assert document["kb_id"] == base["id"]
    assert "job_id" not in payload  # 没有 job —— 上传与解析已解耦

    # 等一会也不会离开 uploaded（没有 job 可消费）
    time.sleep(0.5)
    current = client.get(f"/knowledge/documents/{document['id']}", headers=user).json()[
        "document"
    ]
    assert current["status"] == "uploaded"

    # 库计数 + 按库过滤
    bases = client.get("/knowledge/bases", headers=admin).json()["bases"]
    assert bases[0]["doc_count"] == 1
    docs = client.get(
        f"/knowledge/documents?kb_id={base['id']}", headers=user
    ).json()["documents"]
    assert [d["id"] for d in docs] == [document["id"]]
    assert (
        client.get("/knowledge/documents?kb_id=other", headers=user).json()["documents"]
        == []
    )

    # 普通用户能看到未解析文档（决策：读接口不分角色）
    assert docs[0]["status"] == "uploaded"


def test_upload_to_missing_base_404_and_validation(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, _ = _admin_and_user(client)

    response = client.post(
        "/knowledge/bases/missing/documents",
        headers=admin,
        files={"file": ("a.md", b"# x", "text/markdown")},
    )
    assert response.status_code == 404

    base = _create_base(client, admin)
    response = client.post(
        f"/knowledge/bases/{base['id']}/documents",
        headers=admin,
        files={"file": ("page.html", b"<p>x</p>", "text/html")},
    )
    assert response.status_code == 400
    assert "不支持的文件格式" in response.json()["detail"]

    big = b"x" * (2 * 1024 * 1024)  # 限制 1MB
    response = client.post(
        f"/knowledge/bases/{base['id']}/documents",
        headers=admin,
        files={"file": ("big.md", big, "text/markdown")},
    )
    assert response.status_code == 413


def test_parse_then_poll_status_and_delete(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)
    base = _create_base(client, admin)

    document = _upload(client, admin, base["id"])["document"]

    # 步骤③：批量解析 —— uploaded 入队；不存在/状态不合格的跳过
    parse = client.post(
        "/knowledge/documents/parse",
        headers=admin,
        json={"document_ids": [document["id"], "missing-doc", document["id"]]},
    )
    assert parse.status_code == 202
    body = parse.json()
    assert [q["id"] for q in body["queued"]] == [document["id"]]
    assert body["queued"][0]["job_id"]
    assert [s["id"] for s in body["skipped"]] == ["missing-doc"]

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

    # chunk 已落库（事实源先行），普通用户可读，且冗余 kb_id
    chunks = client.get(
        f"/knowledge/documents/{document['id']}/chunks", headers=user
    ).json()["chunks"]
    assert len(chunks) >= 1
    assert chunks[0]["kb_id"] == base["id"]

    # failed → 可重试（202，retry_count 递增）
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


def test_delete_base_cascades(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, user = _admin_and_user(client)
    base = _create_base(client, admin)
    first = _upload(client, admin, base["id"])["document"]
    second = _upload(client, admin, base["id"])["document"]

    from hermes_constants import get_hermes_home

    paths = [get_hermes_home() / d["file_path"] for d in (first, second)]
    assert all(p.exists() for p in paths)

    response = client.delete(f"/knowledge/bases/{base['id']}", headers=admin)
    assert response.status_code == 200
    assert response.json()["documents"] == 2

    assert client.get("/knowledge/bases", headers=user).json()["bases"] == []
    assert client.get("/knowledge/documents", headers=user).json()["documents"] == []
    assert all(not p.exists() for p in paths)


def test_retry_rejects_active_document(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin, _ = _admin_and_user(client)
    base = _create_base(client, admin)

    doc_id = _upload(client, admin, base["id"])["document"]["id"]
    # uploaded 状态不能 retry（它还没被解析过，用批量 parse 入口）
    response = client.post(f"/knowledge/documents/{doc_id}/retry", headers=admin)
    assert response.status_code == 409
