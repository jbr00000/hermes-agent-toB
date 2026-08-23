"""Agent 任务的运行级知识库选择：plan/execute/retry 的 knowledge 参数。

语义：字段缺省 = 保持现状（挂 knowledge 工具集、全库可检索）；
enabled=false 本轮摘除 knowledge_search；kb_id 把检索限定到指定库。
校验全部前置——被拒的请求不能留下任何运行副作用。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, knowledge_deployment: bool = True) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True)
    if knowledge_deployment:
        (home / "deployment.yaml").write_text(
            "knowledge:\n"
            "  enabled: true\n"
            "  es_url: http://elasticsearch:19200\n"
            "  milvus_uri: http://milvus:19530\n"
            "  embedding:\n"
            "    base_url: http://llm-gw.internal/v1\n"
            "    model: bge-m3\n",
            encoding="utf-8",
        )
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


def _create_task(client: TestClient, headers: dict[str, str]) -> dict:
    created = client.post("/tasks", headers=headers, json={})
    assert created.status_code == 201
    return created.json()["task"]


def _capture_build_agent(monkeypatch) -> list[dict]:
    """monkeypatch build_agent，记录每次调用的 knowledge 相关 kwargs。"""
    captured: list[dict] = []

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db"]

        def chat(self, message, stream_callback=None, task_id=None):
            if stream_callback:
                stream_callback("计划正文")
            return "计划正文"

        def interrupt(self, message=None):
            return None

    import server.agent_factory as agent_factory

    def build_agent(**kwargs):
        captured.append(
            {
                "knowledge_enabled": kwargs.get("knowledge_enabled"),
                "knowledge_kb_id": kwargs.get("knowledge_kb_id"),
                "knowledge_kb_name": kwargs.get("knowledge_kb_name"),
            }
        )
        return FakeAgent()

    monkeypatch.setattr(agent_factory, "build_agent", build_agent)
    return captured


def test_plan_default_keeps_knowledge_enabled(monkeypatch, tmp_path) -> None:
    """不传 knowledge 字段 = 向后兼容：knowledge 工具集照挂、不限定库。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = _create_task(client, headers)
    captured = _capture_build_agent(monkeypatch)

    response = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "检查一下费用"},
    )
    assert response.status_code == 200
    assert captured == [
        {"knowledge_enabled": True, "knowledge_kb_id": None, "knowledge_kb_name": None}
    ]


def test_plan_with_knowledge_disabled(monkeypatch, tmp_path) -> None:
    """enabled=false：worker 侧 build_agent 收到 knowledge_enabled=False。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = _create_task(client, headers)
    captured = _capture_build_agent(monkeypatch)

    response = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "检查一下费用", "knowledge": {"enabled": False}},
    )
    assert response.status_code == 200
    assert captured[0]["knowledge_enabled"] is False
    assert captured[0]["knowledge_kb_id"] is None


def test_plan_with_kb_scope_passes_kb_id_and_name(monkeypatch, tmp_path) -> None:
    """kb_id 限定：库名校验后随 job 传给 build_agent。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    base = client.post("/knowledge/bases", headers=headers, json={"name": "财务制度"})
    assert base.status_code == 201
    kb = base.json()["base"]
    task = _create_task(client, headers)
    captured = _capture_build_agent(monkeypatch)

    response = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "查一下报销制度", "knowledge": {"enabled": True, "kb_id": kb["id"]}},
    )
    assert response.status_code == 200
    assert captured[0]["knowledge_enabled"] is True
    assert captured[0]["knowledge_kb_id"] == kb["id"]
    assert captured[0]["knowledge_kb_name"] == "财务制度"


def test_knowledge_scope_validation_errors(monkeypatch, tmp_path) -> None:
    """非法组合全部前置拒绝：不给任务留下运行副作用。"""
    client = _client(monkeypatch, tmp_path)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    task = _create_task(client, headers)

    missing_kb = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "x", "knowledge": {"enabled": True, "kb_id": "kb-missing"}},
    )
    assert missing_kb.status_code == 404

    disabled_with_kb = client.post(
        f"/tasks/{task['id']}/plan",
        headers=headers,
        json={"message": "x", "knowledge": {"enabled": False, "kb_id": "kb-1"}},
    )
    assert disabled_with_kb.status_code == 400

    detail = client.get(f"/tasks/{task['id']}", headers=headers).json()["task"]
    assert detail["runs"] == []  # 两次被拒都没有入队
    assert detail["status"] == "draft"


def test_knowledge_scope_requires_deployment_and_feature(monkeypatch, tmp_path) -> None:
    """enabled=true 需要部署启用知识库 + 用户有 knowledge feature。"""
    # 部署未启用知识库：显式 enabled=true → 409
    plain_client = _client(monkeypatch, tmp_path / "plain", knowledge_deployment=False)
    plain_headers = _login(plain_client, "admin", "correct-horse-battery-staple")
    plain_task = _create_task(plain_client, plain_headers)
    denied = plain_client.post(
        f"/tasks/{plain_task['id']}/plan",
        headers=plain_headers,
        json={"message": "x", "knowledge": {"enabled": True}},
    )
    assert denied.status_code == 409

    # 部署启用但用户 knowledge feature 被关 → 403
    client = _client(monkeypatch, tmp_path / "kb", knowledge_deployment=True)
    admin_headers = _login(client, "admin", "correct-horse-battery-staple")
    created = client.post(
        "/users",
        headers=admin_headers,
        json={
            "username": "no-kb",
            "password": "password-123",
            "role": "user",
            "features": {"knowledge": False},
        },
    )
    assert created.status_code == 200
    _login(client, "no-kb", "password-123")
    changed = client.post(
        "/auth/change-password",
        headers=_login(client, "no-kb", "password-123"),
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    assert changed.status_code == 200
    user_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    task = _create_task(client, user_headers)
    forbidden = client.post(
        f"/tasks/{task['id']}/plan",
        headers=user_headers,
        json={"message": "x", "knowledge": {"enabled": True}},
    )
    assert forbidden.status_code == 403
    assert "knowledge" in forbidden.json()["detail"]

    # 同一用户 enabled=false（不带知识库）不受 feature 限制
    captured = _capture_build_agent(monkeypatch)
    allowed = client.post(
        f"/tasks/{task['id']}/plan",
        headers=user_headers,
        json={"message": "x", "knowledge": {"enabled": False}},
    )
    assert allowed.status_code == 200
    assert captured[0]["knowledge_enabled"] is False
