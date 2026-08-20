"""POST/GET/DELETE /uploads —— chat/agent 临时附件的存储与解析 API。"""
from __future__ import annotations

from pathlib import Path

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

    from server import uploads

    # 测试里同步解析：上传响应返回时状态已就绪/失败，无需轮询
    monkeypatch.setattr(uploads, "_dispatch_parse", uploads.parse_upload)

    from server.app import create_app

    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _home(client: TestClient) -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _make_chat_session(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post("/sessions", headers=headers, json={"interaction_type": "chat"})
    assert created.status_code == 201
    return created.json()["session"]["id"]


def _upload(
    client: TestClient,
    headers: dict[str, str],
    owner_type: str,
    owner_id: str,
    files: list[tuple[str, bytes]],
):
    return client.post(
        "/uploads",
        headers=headers,
        data={"owner_type": owner_type, "owner_id": owner_id},
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
    )


def test_upload_txt_parses_to_ready_with_token_count(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)

    response = _upload(client, headers, "session", session_id, [("案情摘要.txt", "第一段。\n\n第二段。".encode("utf-8"))])
    assert response.status_code == 201
    record = response.json()["files"][0]
    assert record["parse_status"] == "ready"
    assert record["parser"] == "local"
    assert record["token_count"] > 0
    assert record["owner_type"] == "session"
    assert record["owner_id"] == session_id

    # 原件与解析产物都落在 $HERMES_HOME/uploads/ 下
    home = _home(client)
    assert (home / record["file_path"]).read_bytes() == "第一段。\n\n第二段。".encode("utf-8")
    assert "第一段" in (home / record["parsed_path"]).read_text(encoding="utf-8")

    listed = client.get(
        "/uploads", headers=headers, params={"owner_type": "session", "owner_id": session_id}
    )
    assert listed.status_code == 200
    assert [f["id"] for f in listed.json()["files"]] == [record["id"]]


def test_upload_rejects_bad_ext_empty_and_oversize(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)

    bad_ext = _upload(client, headers, "session", session_id, [("evil.exe", b"MZ")])
    assert bad_ext.status_code == 400

    empty = _upload(client, headers, "session", session_id, [("empty.txt", b"")])
    assert empty.status_code == 400

    from server import uploads

    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 8)
    oversize = _upload(client, headers, "session", session_id, [("big.txt", b"x" * 16)])
    assert oversize.status_code == 413

    listed = client.get(
        "/uploads", headers=headers, params={"owner_type": "session", "owner_id": session_id}
    ).json()["files"]
    assert listed == []


def test_five_file_limit_per_owner(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)

    batch = _upload(
        client,
        headers,
        "session",
        session_id,
        [(f"f{i}.txt", f"content {i}".encode()) for i in range(4)],
    )
    assert batch.status_code == 201
    fifth = _upload(client, headers, "session", session_id, [("f4.txt", b"content 4")])
    assert fifth.status_code == 201
    sixth = _upload(client, headers, "session", session_id, [("f5.txt", b"content 5")])
    assert sixth.status_code == 400
    assert "最多上传 5 个文件" in sixth.json()["detail"]


def test_uploads_are_user_scoped(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, admin_headers)
    record = _upload(client, admin_headers, "session", session_id, [("a.txt", b"secret")]).json()[
        "files"
    ][0]

    client.post(
        "/users",
        headers=admin_headers,
        json={"username": "other", "password": "password-123", "role": "user"},
    )
    other_headers = _login(client, "other", "password-123")
    changed = client.post(
        "/auth/change-password",
        headers=other_headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    other_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    # 别人的 owner：上传 404、列表按 user 过滤为空、删除 404
    assert _upload(client, other_headers, "session", session_id, [("b.txt", b"x")]).status_code == 404
    listed = client.get(
        "/uploads", headers=other_headers, params={"owner_type": "session", "owner_id": session_id}
    ).json()["files"]
    assert listed == []
    assert client.delete(f"/uploads/{record['id']}", headers=other_headers).status_code == 404


def test_delete_upload_removes_disk_files(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)
    record = _upload(client, headers, "session", session_id, [("a.txt", b"hello")]).json()["files"][
        0
    ]
    home = _home(client)
    assert (home / record["file_path"]).exists()

    deleted = client.delete(f"/uploads/{record['id']}", headers=headers)
    assert deleted.status_code == 200
    assert not (home / record["file_path"]).exists()
    assert not (home / record["parsed_path"]).exists()
    assert client.get(
        "/uploads", headers=headers, params={"owner_type": "session", "owner_id": session_id}
    ).json()["files"] == []
    assert client.delete(f"/uploads/{record['id']}", headers=headers).status_code == 404


def test_session_delete_cascades_uploads(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)
    record = _upload(client, headers, "session", session_id, [("a.txt", b"hello")]).json()["files"][
        0
    ]
    home = _home(client)
    assert (home / record["file_path"]).exists()

    deleted = client.delete(f"/sessions/{session_id}", headers=headers)
    assert deleted.status_code == 200
    assert not (home / record["file_path"]).exists()


def test_task_owner_uploads_and_agent_session_rejected(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    task = client.post("/tasks", headers=headers, json={}).json()["task"]
    ok = _upload(client, headers, "task", task["id"], [("需求.xlsx", b"")])
    # xlsx 空文件 openpyxl 会解析失败，但上传本身应受理（异步解析）
    assert ok.status_code == 400  # 空文件在读取层被拦下
    ok = _upload(client, headers, "task", task["id"], [("需求说明.txt", "需求正文".encode())])
    assert ok.status_code == 201
    assert ok.json()["files"][0]["parse_status"] == "ready"

    # agent 会话不能直接作为 session owner——附件要挂到 task 上
    rejected = _upload(client, headers, "session", task["session_id"], [("a.txt", b"x")])
    assert rejected.status_code == 400

    # 任务删除后磁盘目录清空
    record = ok.json()["files"][0]
    home = _home(client)
    assert (home / record["file_path"]).exists()
    assert client.delete(f"/tasks/{task['id']}", headers=headers).status_code == 200
    assert not (home / record["file_path"]).exists()


def test_pdf_without_mineru_fails_with_clear_error(monkeypatch, tmp_path) -> None:
    """未配置 MinerU 的部署：PDF 上传受理但解析 failed，错误信息可展示。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)

    response = _upload(client, headers, "session", session_id, [("合同.pdf", b"%PDF-1.4 fake")])
    assert response.status_code == 201
    record = response.json()["files"][0]
    assert record["parse_status"] == "failed"
    assert "mineru" in (record["parse_error"] or "").lower()


# ---------------------------------------------------------------- token 预算与截断


def test_list_uploads_includes_budget_summary(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)
    _upload(client, headers, "session", session_id, [("a.txt", "若干正文".encode())])

    listed = client.get(
        "/uploads", headers=headers, params={"owner_type": "session", "owner_id": session_id}
    ).json()
    budget = listed["budget"]
    assert budget["max_input_tokens"] == 128_000
    assert budget["file_tokens"] > 0
    assert budget["over_budget"] is False
    # 全新会话无历史：预算 = 上限 − system 粗估 − 输出余量
    assert budget["budget_tokens"] == 128_000 - 4096 - 8192


def test_list_uploads_flags_over_budget_without_blocking(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)
    _upload(client, headers, "session", session_id, [("a.txt", "若干正文".encode())])

    from server import runtime_config

    monkeypatch.setattr(
        runtime_config,
        "load_runtime_config",
        lambda: runtime_config.RuntimeConfig(
            provider="custom", model="m", reasoning_config=None, max_input_tokens=10_000
        ),
    )
    budget = client.get(
        "/uploads", headers=headers, params={"owner_type": "session", "owner_id": session_id}
    ).json()["budget"]
    assert budget["budget_tokens"] == 0  # 10000 − 4096 − 8192 < 0 → 0
    assert budget["over_budget"] is True


def test_attachment_block_truncates_newest_first(monkeypatch) -> None:
    """超预算时最早上传的保全量，最新的先被截断；预算耗尽则后续跳过。"""
    from server import uploads

    texts = {"1": "甲" * 400, "2": "乙" * 400}
    monkeypatch.setattr(uploads, "read_parsed_text", lambda record: texts[record["id"]])
    records = [
        {
            "id": file_id,
            "file_name": name,
            "parse_status": "ready",
            "token_count": uploads.count_tokens(texts[file_id]),
        }
        for file_id, name in (("1", "first.txt"), ("2", "second.txt"))
    ]

    budget = records[0]["token_count"] + 10
    block, usage = uploads.build_attachment_block(records, budget)
    assert [u["status"] for u in usage] == ["full", "truncated"]
    assert usage[1]["included_tokens"] == 10
    assert "已截断，仅展示前 10 tokens" in block
    assert "first.txt" in block and "second.txt" in block

    block, usage = uploads.build_attachment_block(records, 5)
    assert [u["status"] for u in usage] == ["truncated", "skipped"]
    assert "上下文预算不足，未注入内容" in block

    # 解析中/失败的附件不参与注入
    parsing = [{"id": "3", "file_name": "p.txt", "parse_status": "parsing", "token_count": 0}]
    block, usage = uploads.build_attachment_block(parsing, 100)
    assert block == "" and usage == []


def test_runtime_config_reads_max_input_tokens(monkeypatch) -> None:
    from server import runtime_config

    monkeypatch.setattr(
        runtime_config,
        "load_config",
        lambda: {"model": {"default": "m", "max_input_tokens": 64_000}},
    )
    assert runtime_config.load_runtime_config().max_input_tokens == 64_000

    monkeypatch.setattr(runtime_config, "load_config", lambda: {"model": "m"})
    assert runtime_config.load_runtime_config().max_input_tokens == 128_000


# ------------------------------------------------------- 消息注入 + 沙箱暂存


def _capture_agent(monkeypatch, captured: list[dict]) -> None:
    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "session_search"]

        def __init__(self, callbacks: dict | None = None) -> None:
            self.callbacks = callbacks or {}

        def chat(self, message, stream_callback=None, task_id=None):
            captured.append({"message": message, "mode": self.callbacks.get("mode")})
            return "回答"

        def interrupt(self, message=None):
            return None

    import server.agent_factory as agent_factory

    monkeypatch.setattr(
        agent_factory, "build_agent", lambda **kwargs: FakeAgent(kwargs)
    )


def test_chat_turn_injects_attachment_once_and_persists(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _make_chat_session(client, headers)
    _upload(client, headers, "session", session_id, [("案情.txt", "张三诉李四合同纠纷。".encode())])

    captured: list[dict] = []
    _capture_agent(monkeypatch, captured)

    first = client.post(
        "/chat",
        headers=headers,
        json={"session_id": session_id, "interaction_type": "chat", "message": "总结案情"},
    )
    assert first.status_code == 200
    assert "【附件 1/1：案情.txt】" in captured[0]["message"]
    assert "张三诉李四合同纠纷。" in captured[0]["message"]
    assert captured[0]["message"].startswith("总结案情")

    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    user_msg = detail["messages"][0]
    assert "张三诉李四合同纠纷。" in user_msg["content"]  # 注入文本随历史落库
    assert user_msg["metadata"]["display_content"] == "总结案情"
    assert user_msg["metadata"]["attachments"][0]["status"] == "full"

    # 第二轮：同一文件不重复注入（历史里已有，前缀缓存命中）
    second = client.post(
        "/chat",
        headers=headers,
        json={"session_id": session_id, "interaction_type": "chat", "message": "继续"},
    )
    assert second.status_code == 200
    assert captured[1]["message"] == "继续"

    # 对话中途新传文件 → 下一轮补注（老文件仍不重复）
    _upload(client, headers, "session", session_id, [("补充.txt", "补充证据清单。".encode())])
    third = client.post(
        "/chat",
        headers=headers,
        json={"session_id": session_id, "interaction_type": "chat", "message": "再看补充"},
    )
    assert third.status_code == 200
    assert "补充证据清单。" in captured[2]["message"]
    assert "张三诉李四" not in captured[2]["message"]


def test_agent_task_plan_injection_and_execute_workspace(monkeypatch, tmp_path) -> None:
    """plan 注入附件全文；execute 不注文本，原件在沙箱工作区 uploads/ 且有位置说明。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = client.post("/tasks", headers=headers, json={}).json()["task"]
    record = _upload(
        client, headers, "task", task["id"], [("费用明细.txt", "律师费 3000 元/小时。".encode())]
    ).json()["files"][0]

    captured: list[dict] = []
    _capture_agent(monkeypatch, captured)

    assert client.post(
        f"/tasks/{task['id']}/plan", headers=headers, json={"message": "测算费用"}
    ).status_code == 200
    assert captured[-1]["mode"] == "plan"
    assert "律师费 3000 元/小时。" in captured[-1]["message"]

    assert client.post(f"/tasks/{task['id']}/approve", headers=headers).status_code == 200
    assert client.put(
        f"/tasks/{task['id']}/permission", headers=headers, json={"mode": "full"}
    ).status_code == 200
    assert client.post(
        f"/tasks/{task['id']}/execute", headers=headers, json={}
    ).status_code == 200
    execute_message = captured[-1]["message"]
    assert captured[-1]["mode"] == "execute"
    # execute 不注入全文，只给工作区位置说明
    assert "律师费 3000 元/小时。" not in execute_message
    assert f"/workspace/tasks/{task['id']}/uploads/" in execute_message
    assert "费用明细.txt" in execute_message

    # 原件确实落进了沙箱任务工作区（宿主机侧，绑定挂载进容器）
    from server.sandbox import task_workspace_dir

    staged = task_workspace_dir(task["user_id"], task["id"]) / "uploads" / f"{record['id']}.txt"
    assert staged.read_bytes() == "律师费 3000 元/小时。".encode()

    # 交付文件列表排除 uploads/——输入附件不是交付物
    artifacts = client.get(f"/tasks/{task['id']}/artifacts", headers=headers).json()["artifacts"]
    assert all(not a["path"].startswith("uploads/") for a in artifacts)

    # 删除附件：原件目录与沙箱暂存副本一起清掉
    assert client.delete(f"/uploads/{record['id']}", headers=headers).status_code == 200
    assert not staged.exists()
