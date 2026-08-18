"""知识库问答模式（/chat + mode=knowledge）与 citations 产出/持久化。"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient


def _client(
    monkeypatch, tmp_path, *, knowledge_enabled: bool = False, aux_llm: bool = False
) -> TestClient:
    home = tmp_path / "hermes_home"
    home.mkdir()
    if knowledge_enabled:
        yaml_text = (
            "knowledge:\n"
            "  enabled: true\n"
            "  es_url: http://elasticsearch:19200\n"
            "  milvus_uri: http://milvus:19530\n"
            "  embedding:\n"
            "    base_url: http://llm-gw.internal/v1\n"
            "    model: bge-m3\n"
        )
        if aux_llm:
            yaml_text += (
                "  aux_llm:\n"
                "    base_url: http://llm-gw.internal/v1\n"
                "    model: qwen-27B-FP8\n"
            )
        (home / "deployment.yaml").write_text(yaml_text, encoding="utf-8")
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


def _retrieval_chunk(
    chunk_id: str = "c1",
    doc_id: str = "d1",
    doc_name: str = "财务制度.pdf",
    chunk_title: str = "报销流程",
    content: str = "第一步填写报销单",
    score: float = 0.62,
) -> dict:
    """server.knowledge.retriever.search_chunks 的返回项（MySQL 回表后的形状）。"""
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "doc_name": doc_name,
        "kb_id": "kb-1",
        "chunk_title": chunk_title,
        "content": content,
        "doc_pos": 0,
        "score": score,
    }


def _patch_fast_retrieval(monkeypatch, *, chunks=None, raises: bool = False) -> None:
    """固定快速模式的服务端直接检索结果，避免测试真去打 ES/Milvus。"""
    import server.knowledge.retriever as retriever

    if raises:
        def failing_search(*_args, **_kwargs):
            raise RuntimeError("es unreachable")

        monkeypatch.setattr(retriever, "search_chunks", failing_search)
    else:
        monkeypatch.setattr(retriever, "search_chunks", lambda *a, **k: list(chunks or []))


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
    # 让快速模式的服务端直接检索失败 → 回退到工具路径（本测试验的是工具拦截链路）
    _patch_fast_retrieval(monkeypatch, raises=True)

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
    # 工具路径测试：服务端直接检索置为失败，走 knowledge_search 工具拦截
    _patch_fast_retrieval(monkeypatch, raises=True)

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


def test_search_mode_rejected_outside_knowledge_mode(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")

    denied = client.post(
        "/chat",
        headers=headers,
        json={"message": "hi", "interaction_type": "chat", "search_mode": "precise"},
    )
    assert denied.status_code == 400


def test_precise_mode_degrades_to_fast_without_aux_llm(monkeypatch, tmp_path) -> None:
    """未配置辅助模型时 precise 静默降级 fast（不算错误）。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)  # 无 aux_llm 段
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)
    # 降级为 fast 后会走服务端直接检索；置空结果让注入内容确定
    _patch_fast_retrieval(monkeypatch, chunks=[])

    captured: dict[str, object] = {}

    from server.knowledge import request_context

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            captured["search_mode"] = request_context.get_search_mode()
            return "知识库中未找到相关内容。"

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory

    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: FakeAgent())

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "interaction_type": "chat",
            "mode": "knowledge",
            "search_mode": "precise",
            "message": "报销流程？",
        },
    )
    assert response.status_code == 200
    assert captured["search_mode"] == "fast"  # 降级
    # 无改写钉注；fast 直接检索为空时缀"未检索到"说明（原文仍在开头）
    message = str(captured["message"])
    assert message.startswith("报销流程？")
    assert "未检索到相关内容" in message


def test_precise_mode_rewrites_followup_query(monkeypatch, tmp_path) -> None:
    """精准模式：有历史时先用轻量模型改写，改写结果缀在当前 user 轮次尾部。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True, aux_llm=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)

    from server.storage import get_repository

    repository = get_repository()
    repository.append_message(session_id, "user", "报销流程是什么？")
    repository.append_message(session_id, "assistant", "报销分三步【1】……")

    captured: dict[str, object] = {}

    from server.knowledge import request_context

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            captured["search_mode"] = request_context.get_search_mode()
            return "额度上限是 5000 元【1】。"

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory
    import server.knowledge.query_rewrite as query_rewrite

    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: FakeAgent())
    rewrite_calls: list[dict] = []

    def fake_rewrite(query, history, *, config):
        rewrite_calls.append({"query": query, "history": history})
        return "财务制度的报销额度上限是多少？"

    monkeypatch.setattr(query_rewrite, "rewrite_query_with_history", fake_rewrite)

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "interaction_type": "chat",
            "mode": "knowledge",
            "search_mode": "precise",
            "message": "那额度上限呢？",
        },
    )
    assert response.status_code == 200
    assert captured["search_mode"] == "precise"
    # 改写结果以轮次内提示缀在原问题尾部发给模型（不动 system prompt）
    message = str(captured["message"])
    assert message.startswith("那额度上限呢？")
    assert "财务制度的报销额度上限是多少？" in message
    # 改写拿到了追问原文与会话历史
    assert rewrite_calls[0]["query"] == "那额度上限呢？"
    assert any(m["role"] == "assistant" for m in rewrite_calls[0]["history"])
    # 落库的 user 消息仍是用户原文
    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    assert detail["messages"][-2]["content"] == "那额度上限呢？"


def test_fast_mode_skips_rewrite(monkeypatch, tmp_path) -> None:
    """默认 fast：不调用改写，直接服务端检索（检索结果为空时注入"未检索到"）。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True, aux_llm=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)
    _patch_fast_retrieval(monkeypatch, chunks=[])

    captured: dict[str, object] = {}

    from server.knowledge import request_context

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            captured["search_mode"] = request_context.get_search_mode()
            return "好的"

        def interrupt(self, message=None):
            pass

    import server.agent_factory as agent_factory
    import server.knowledge.query_rewrite as query_rewrite

    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: FakeAgent())
    monkeypatch.setattr(
        query_rewrite,
        "rewrite_query_with_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fast 不应改写")),
    )

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "interaction_type": "chat",
            "mode": "knowledge",
            "message": "报销流程？",
        },
    )
    assert response.status_code == 200
    assert captured["search_mode"] == "fast"
    assert str(captured["message"]).startswith("报销流程？")


def test_fast_mode_direct_retrieval_injects_chunks(monkeypatch, tmp_path) -> None:
    """快速模式：服务端直接检索，分块以【N】块注入当前 user 轮次尾部——
    模型无需先调 knowledge_search 即可回答，引用卡片照常产出/落库。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)

    from server.storage import get_repository

    repository = get_repository()
    admin = repository.get_user_by_username("admin")
    base = repository.create_knowledge_base(name="制度库", creator_id=admin["id"])

    retrieval_calls: list[dict] = []

    import server.knowledge.retriever as retriever

    def fake_search(query, *, kb_id=None, topk=None, config=None):
        retrieval_calls.append({"query": query, "kb_id": kb_id})
        return [
            _retrieval_chunk(chunk_id="c1", score=0.62),
            _retrieval_chunk(
                chunk_id="c2", doc_id="d2", doc_name="考勤制度.pdf",
                chunk_title="请假", content="请假需提前一天", score=0.31,
            ),
        ]

    monkeypatch.setattr(retriever, "search_chunks", fake_search)

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            return "报销第一步是填写报销单【1】。"

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
            "request_id": "req-fast-direct",
            "interaction_type": "chat",
            "mode": "knowledge",
            "kb_id": base["id"],
            "message": "报销流程是什么？",
        },
    )

    assert response.status_code == 200
    # 服务端检索用用户原文 + 选定库限定
    assert retrieval_calls == [{"query": "报销流程是什么？", "kb_id": base["id"]}]

    # 注入：原文开头 + 带编号的来源块缀在尾部（不经工具调用）
    message = str(captured["message"])
    assert message.startswith("报销流程是什么？")
    assert "【1】《财务制度.pdf》·报销流程" in message
    assert "【2】《考勤制度.pdf》·请假" in message
    assert "第一步填写报销单" in message

    # SSE：检索完成即推 citations（不等模型回答完）
    lines = response.text.splitlines()
    citation_events = [
        json.loads(lines[index + 1].removeprefix("data: "))
        for index, line in enumerate(lines)
        if line.startswith("event: citations") and lines[index + 1].startswith("data: ")
    ]
    assert len(citation_events) == 1
    assert [c["chunk_id"] for c in citation_events[0]["chunks"]] == ["c1", "c2"]

    # 落库仍按【N】过滤：回答只标【1】→ 只留 c1
    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    citations = detail["messages"][-1]["metadata"]["citations"]
    assert [c["chunk_id"] for c in citations] == ["c1"]


def test_fast_mode_retrieval_failure_falls_back_to_tool(monkeypatch, tmp_path) -> None:
    """服务端直接检索抛错 → 静默回退工具路径（模型自行调 knowledge_search）。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)
    _patch_fast_retrieval(monkeypatch, raises=True)

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            captured["tool_complete_callback"](
                "tc-1", "knowledge_search", {"query": "报销流程"}, _knowledge_tool_result()
            )
            return "报销分三步【1】"

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
            "request_id": "req-fast-fallback",
            "interaction_type": "chat",
            "mode": "knowledge",
            "message": "报销流程是什么？",
        },
    )

    assert response.status_code == 200
    # 检索失败时不注入任何内容，原文直发，由模型走工具
    assert captured["message"] == "报销流程是什么？"
    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    citations = detail["messages"][-1]["metadata"]["citations"]
    assert [c["chunk_id"] for c in citations] == ["c1"]


def test_fast_mode_empty_result_injects_not_found(monkeypatch, tmp_path) -> None:
    """检索结果为空：注入"未检索到"说明、不发 citations 事件；模型如实回答
    未找到时不落 citations metadata。"""
    client = _client(monkeypatch, tmp_path, knowledge_enabled=True)
    headers = _login(client, "admin", "correct-horse-battery-staple")
    session_id = _chat_session(client, headers)
    _patch_fast_retrieval(monkeypatch, chunks=[])

    captured: dict[str, object] = {}

    class FakeAgent:
        provider = "custom"
        model = "test-model"
        reasoning_config = {"enabled": False}
        enabled_toolsets = ["knowledge"]

        def chat(self, message, stream_callback=None):
            captured["message"] = message
            return "知识库中未找到相关内容。"

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
            "request_id": "req-fast-empty",
            "interaction_type": "chat",
            "mode": "knowledge",
            "message": "报销流程是什么？",
        },
    )

    assert response.status_code == 200
    assert "（知识库检索结果：未检索到相关内容）" in str(captured["message"])
    assert "event: citations" not in response.text
    detail = client.get(f"/sessions/{session_id}", headers=headers).json()
    assert detail["messages"][-1]["metadata"] is None


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
