"""知识库问答模式（/chat + mode=knowledge）与 citations 产出/持久化。"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, knowledge_enabled: bool = False) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    if knowledge_enabled:
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


def _chat_session(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post(
        "/sessions", headers=headers, json={"interaction_type": "chat"}
    )
    assert created.status_code == 201
    return created.json()["session"]["id"]


def _knowledge_tool_result() -> str:
    return json.dumps(
        {
            "query": "报销流程",
            "total": 1,
            "chunks": [
                {
                    "num": 1,
                    "chunk_id": "c1",
                    "doc_id": "d1",
                    "doc_name": "财务制度.pdf",
                    "chunk_title": "报销流程",
                    "content": "第一步填写报销单" * 30,  # 超 200 字符，验证 snippet 截断
                    "score": 0.62,
                }
            ],
        },
        ensure_ascii=False,
    )


def test_knowledge_mode_requires_knowledge_feature(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    super_headers = _login(client, "admin", "correct-horse-battery-staple")
    created = client.post(
        "/users",
        headers=super_headers,
        json={"username": "no-kb", "password": "password-123", "features": {"knowledge": False}},
    )
    assert created.status_code == 200
    headers = _login(client, "no-kb", "password-123")
    changed = client.post(
        "/auth/change-password",
        headers=headers,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    denied = client.post(
        "/chat",
        headers=headers,
        json={"message": "报销流程？", "interaction_type": "chat", "mode": "knowledge"},
    )
    assert denied.status_code == 403
    assert "knowledge" in denied.json()["detail"]
    # 被拒请求不得留下会话等副作用
    assert client.get("/sessions?interaction_type=chat", headers=headers).json() == {"sessions": []}


def test_knowledge_mode_requires_enabled_deployment(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=False)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    denied = client.post(
        "/chat",
        headers=headers,
        json={"message": "报销流程？", "interaction_type": "chat", "mode": "knowledge"},
    )
    assert denied.status_code == 409
    assert "未启用" in denied.json()["detail"]


def test_kb_id_rejected_outside_knowledge_mode(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    denied = client.post(
        "/chat",
        headers=headers,
        json={"message": "hi", "interaction_type": "chat", "kb_id": "kb-1"},
    )
    assert denied.status_code == 400


def test_kb_id_unknown_returns_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    denied = client.post(
        "/chat",
        headers=headers,
        json={
            "message": "报销流程？",
            "interaction_type": "chat",
            "mode": "knowledge",
            "kb_id": "kb-missing",
        },
    )
    assert denied.status_code == 404


def test_knowledge_mode_streams_citations_and_persists_metadata(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)

    from server.storage import get_repository

    repository = get_repository()
    admin = repository.get_user_by_username("admin")
    base = repository.create_knowledge_base(name="制度库", creator_id=admin["id"])

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["tool_complete_callback"](
                "tc-1", "knowledge_search", {"query": "报销流程"}, _knowledge_tool_result()
            )
            if stream_callback:
                stream_callback("报销分三步【1】")
            return "报销分三步【1】"

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory

    def build_agent(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr(agent_factory, "build_agent", build_agent)

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": "req-kb-1",
            "interaction_type": "chat",
            "mode": "knowledge",
            "kb_id": base["id"],
            "message": "报销流程是什么？",
        },
    )

    assert response.status_code == 200
    # knowledge 模式：mode 与选库限定透传到 build_agent
    assert captured["mode"] == "knowledge"
    assert captured["knowledge_kb_id"] == base["id"]
    assert captured["knowledge_kb_name"] == "制度库"

    # SSE：citations 事件带出去重后的引用卡片
    lines = response.text.splitlines()
    citation_events = [
        json.loads(lines[index + 1].removeprefix("data: "))
        for index, line in enumerate(lines)
        if line.startswith("event: citations") and lines[index + 1].startswith("data: ")
    ]
    assert len(citation_events) == 1
    chunk = citation_events[0]["chunks"][0]
    assert chunk["chunk_id"] == "c1"
    assert chunk["doc_name"] == "财务制度.pdf"
    assert len(chunk["snippet"]) == 200  # 截断

    # 持久化：assistant 消息 metadata 带 citations，刷新后仍可渲染
    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    assistant = detail["messages"][-1]
    assert assistant["role"] == "assistant"
    citations = assistant["metadata"]["citations"]
    assert [c["chunk_id"] for c in citations] == ["c1"]
    assert citations[0]["score"] == 0.62


def test_plain_chat_has_no_kb_scoping(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["db", "web", "knowledge"]

        def chat(self, message, stream_callback=None):
            return "好的"

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory

    monkeypatch.setattr(
        agent_factory,
        "build_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": "req-plain-1",
            "interaction_type": "chat",
            "message": "你好",
        },
    )
    assert response.status_code == 200
    assert captured["mode"] == "chat"
    assert captured["knowledge_kb_id"] is None

    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    assert detail["messages"][-1]["metadata"] is None


def _two_chunk_tool_result() -> str:
    return json.dumps(
        {
            "query": "报销",
            "total": 2,
            "chunks": [
                {
                    "num": 1,
                    "chunk_id": "c1",
                    "doc_id": "d1",
                    "doc_name": "财务制度.pdf",
                    "chunk_title": "报销流程",
                    "content": "第一步填写报销单",
                    "score": 0.62,
                },
                {
                    "num": 2,
                    "chunk_id": "c2",
                    "doc_id": "d2",
                    "doc_name": "考勤制度.pdf",
                    "chunk_title": "请假",
                    "content": "请假需提前一天",
                    "score": 0.31,
                },
            ],
        },
        ensure_ascii=False,
    )


def _run_knowledge_chat(
    monkeypatch,
    tmp_path,
    *,
    tool_result: str,
    answer: str,
) -> dict:
    """knowledge 模式跑一次问答（FakeAgent 固定检索结果与回答），返回会话详情。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["tool_complete_callback"](
                "tc-1", "knowledge_search", {"query": "报销"}, tool_result
            )
            return answer

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory

    monkeypatch.setattr(
        agent_factory,
        "build_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "interaction_type": "chat",
            "mode": "knowledge",
            "message": "报销流程是什么？",
        },
    )
    assert response.status_code == 200
    return client.get(f"/sessions/{session_id}", headers=headers).json()


def test_citations_filtered_to_cited_nums(monkeypatch, tmp_path) -> None:
    """卡片与回答口径一致：工具返回 2 块但回答只标【1】→ 持久化只留 chunk 1。"""
    detail = _run_knowledge_chat(
        monkeypatch,
        tmp_path,
        tool_result=_two_chunk_tool_result(),
        answer="报销第一步是填写报销单【1】。",
    )
    citations = detail["messages"][-1]["metadata"]["citations"]
    assert [c["chunk_id"] for c in citations] == ["c1"]


def test_citations_cleared_when_answer_admits_not_found(monkeypatch, tmp_path) -> None:
    """模型回答"未找到"（不标任何【N】）→ metadata 无 citations，卡片清空。"""
    detail = _run_knowledge_chat(
        monkeypatch,
        tmp_path,
        tool_result=_two_chunk_tool_result(),
        answer="知识库中未找到相关内容。",
    )
    assert detail["messages"][-1]["metadata"] is None


def test_cited_num_variants(monkeypatch, tmp_path) -> None:
    """【1、2】连标写法同样被识别（保留两块）。"""
    detail = _run_knowledge_chat(
        monkeypatch,
        tmp_path,
        tool_result=_two_chunk_tool_result(),
        answer="报销看财务制度【1、2】。",
    )
    citations = detail["messages"][-1]["metadata"]["citations"]
    assert [c["chunk_id"] for c in citations] == ["c1", "c2"]


def test_append_message_metadata_roundtrip(monkeypatch, tmp_path) -> None:
    _client(monkeypatch, tmp_path)

    from server.storage import get_repository

    repository = get_repository()
    admin = repository.get_user_by_username("admin")
    conversation = repository.create_conversation(admin["id"], interaction_type="chat")

    repository.append_message(conversation["id"], "user", "问题")
    repository.append_message(
        conversation["id"],
        "assistant",
        "回答【1】",
        metadata={"citations": [{"chunk_id": "c1", "doc_name": "制度.pdf"}]},
    )

    messages = repository.get_messages(conversation["id"])
    assert messages[0]["metadata"] is None
    assert messages[1]["metadata"] == {
        "citations": [{"chunk_id": "c1", "doc_name": "制度.pdf"}]
    }
