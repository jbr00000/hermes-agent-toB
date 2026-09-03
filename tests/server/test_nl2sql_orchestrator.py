"""API tests: /nl2sql/ask 编排器 —— 事件序列契约、重试耗尽失败路径、入参校验。

LLM 与业务库执行均 monkeypatch（CI 无 chat_completions 端点与业务 MySQL）；
断言的是编排不变量：阶段事件顺序、done 契约字段、4 次重试语义、错误文案口径。
"""
from __future__ import annotations

import asyncio
import json

import pytest

QUESTION = "各基金类别下收益为正的基金有多少只？"
FAKE_SQL = (
    "SELECT a.FundType AS fund_type, COUNT(DISTINCT a.InnerCode) AS fund_count "
    "FROM mf_fundarchives a JOIN mf_netvalueperformancehis p ON a.InnerCode = p.InnerCode "
    "WHERE p.RRSinceThisYear > 0 GROUP BY a.FundType ORDER BY fund_count DESC"
)
FAKE_ROWS = [
    {"fund_type": "股票型", "fund_count": 60},
    {"fund_type": "债券型", "fund_count": 42},
    {"fund_type": "货币型", "fund_count": 1},
]


def _client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

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


def _admin(client) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery-staple"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_dataset(client) -> str:
    """建数据源+数据集+一条启用 DDL（含注释行，验证 DDL 清洗），返回 dataset_id。"""
    admin = _admin(client)
    response = client.post("/nl2sql/datasources", headers=admin, json={
        "name": "基金问数库", "db_type": "mysql", "host": "127.0.0.1", "port": 13306,
        "database_name": "nl2sql_fund", "username": "reader", "password": "secret-pw",
    })
    assert response.status_code == 201, response.text
    datasource = response.json()["datasource"]
    response = client.post("/nl2sql/datasets", headers=admin, json={
        "name": "基金数据集", "datasource_id": datasource["id"],
        "description": "公募基金问数", "system_prompt": "你是基金领域问数助手",
    })
    assert response.status_code == 201, response.text
    dataset = response.json()["dataset"]
    response = client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/tables", headers=admin,
        json={"fields": {
            "table_name": "mf_fundarchives",
            "ddl_content": "CREATE TABLE mf_fundarchives (\n  InnerCode int, -- 基金内部代码\n  FundType text\n);",
            "description": "基金档案表", "enabled": True, "provider": "MANUAL",
        }},
    )
    assert response.status_code == 200, response.text
    return dataset["id"]


def _patch_llm(monkeypatch, *, sql_response: str | None = None) -> None:
    """假 LLM：按 system_prompt 分派罐头响应；sql_response=None 时 SQL 生成永远失败。"""
    from server.nl2sql.algorithm.llm import LLMClient

    async def fake_chat_completion(
        self, user_prompt, system_prompt=None, history_messages=None, temperature=0.0, timeout=None
    ):
        sp = system_prompt or ""
        if "实体抽取" in sp:
            return ('<result>{"time_entities": [], "other_entities": ["收益为正的基金"], '
                    '"metric_entities": ["基金数量"]}</result>')
        if "生成SQL" in sp:
            if sql_response is None:
                return "我没法生成 SQL"  # 无 <result> 标签 → sql_gen error
            return sql_response
        if "格式化查询结果" in sp:
            return ('<result>{"type": "bar", "figure_type": "bar", "dimensions": "fund_type", '
                    '"metrics": "fund_count", "content_desc": "分布说明"}</result>')
        if "后处理" in sp:
            return '<result>{"removable_quantity_fields": [], "explicit_quantity_fields": []}</result>'
        return "<result>{}</result>"

    monkeypatch.setattr(LLMClient, "__init__", lambda self: None)
    monkeypatch.setattr(LLMClient, "chat_completion", fake_chat_completion)


def _patch_infra(monkeypatch, *, exec_rows: list[dict] | None = None) -> None:
    """检索降级为空（不碰 ES/Milvus）；execute_sql 返回罐头行；重试 sleep 归零。"""
    from server.nl2sql.algorithm import services
    from server.nl2sql.algorithm.services import SQLExecutionService

    async def fake_hybrid_search(**kwargs):
        return []

    def fake_execute_sql(self, phase_result):
        if exec_rows is None:
            phase_result["exec_result"] = []
            phase_result["sql_exec_status"] = "error"
            phase_result["sql_exec_error_information"] = [
                {"role": "user", "content": "SQL执行报错，错误信息为: boom，请重新调整输出SQL语句"}
            ]
        else:
            phase_result["exec_result"] = exec_rows
            phase_result["exec_truncated"] = False
            phase_result["sql_exec_status"] = "success"
            phase_result["sql_exec_error_information"] = []
        return phase_result

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(services, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(SQLExecutionService, "execute_sql", fake_execute_sql)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def _run_ask(dataset_id: str, question: str = QUESTION):
    from server.nl2sql.algorithm.orchestrator import Nl2sqlOrchestrator

    events: list[tuple[str, dict]] = []
    orchestrator = Nl2sqlOrchestrator(emit=lambda event, payload: events.append((event, payload)))
    result = asyncio.run(orchestrator.ask(question, dataset_id=dataset_id))
    return events, result


def test_ask_happy_path_event_sequence_and_contract(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_dataset(client)
    _patch_llm(monkeypatch, sql_response='<result>%s</result>' % json.dumps(
        {"sql": FAKE_SQL, "explain": "按类别统计收益为正的基金数"}, ensure_ascii=False))
    _patch_infra(monkeypatch, exec_rows=FAKE_ROWS)

    events, result = _run_ask(dataset_id)

    phase_seq = [(p.get("step"), p.get("status")) for e, p in events if e == "phase"]
    assert phase_seq == [
        ("understand", "start"), ("understand", "done"),
        ("generate", "start"), ("generate", "done"),
        ("result", "start"), ("result", "done"),
    ]
    # sql 事件在安检通过后、generate done 之前推送
    sql_events = [p for e, p in events if e == "sql"]
    assert len(sql_events) == 1 and sql_events[0]["sql"] == FAKE_SQL
    generate_done_at = phase_seq.index(("generate", "done"))
    sql_at = [i for i, (e, _p) in enumerate(events) if e == "sql"][0]
    phase_positions = [i for i, (e, p) in enumerate(events) if e == "phase"]
    assert phase_positions[2] < sql_at < phase_positions[generate_done_at]

    assert result["status"] == "success" and result["error"] is None
    assert result["sql_content"] == FAKE_SQL
    assert result["explain_content"] == "按类别统计收益为正的基金数"
    assert result["question"] == QUESTION
    assert result["token_num"] > 0

    assert len(result["format_outputs"]) == 1
    output = result["format_outputs"][0]
    assert output["data"] == FAKE_ROWS
    assert output["data_all"] == len(FAKE_ROWS)
    assert output["result_desc"] == f"查询到{len(FAKE_ROWS)}条数据"
    # 绘图数据抽取：模型建议 bar + 单维度单指标 → data_figure 两列俱全
    assert output["figure_type"] == "bar"
    assert output["dimensions"] == "fund_type" and output["metrics"] == "fund_count"
    assert output["data_figure"] == [
        {"fund_type": row["fund_type"], "fund_count": row["fund_count"]} for row in FAKE_ROWS
    ]

    understand_done = next(p for e, p in events if e == "phase" and p.get("step") == "understand" and p.get("status") == "done")
    assert understand_done["entities"]["other"] == ["收益为正的基金"]
    assert "收益为正的基金" in understand_done["entity_explain"]
    generate_done = next(p for e, p in events if e == "phase" and p.get("step") == "generate" and p.get("status") == "done")
    assert generate_done["rows"] == len(FAKE_ROWS) and generate_done["attempts"] == 1
    assert generate_done["tables"] == ["mf_fundarchives"]


def test_ask_generation_retry_exhaustion_returns_failed(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_dataset(client)
    _patch_llm(monkeypatch, sql_response=None)  # SQL 生成永远失败
    _patch_infra(monkeypatch, exec_rows=FAKE_ROWS)

    events, result = _run_ask(dataset_id)

    assert result["status"] == "failed"
    assert result["error"] == "SQL生成发生未知错误，请重新尝试"
    generate_done = next(p for e, p in events if e == "phase" and p.get("step") == "generate" and p.get("status") == "done")
    assert generate_done["attempts"] == 4  # 共享 4 次尝试耗尽
    result_done = next(p for e, p in events if e == "phase" and p.get("step") == "result" and p.get("status") == "done")
    assert result_done["error"] == result["error"]
    assert [e for e, _p in events].count("sql") == 0  # 从未通过安检
    assert result["format_outputs"][0]["result_desc"] == "查询失败"
    assert result["format_outputs"][0]["data"] == []


def test_ask_input_validation(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_dataset(client)
    _patch_llm(monkeypatch)
    _patch_infra(monkeypatch)

    from server.nl2sql.algorithm import Nl2sqlError
    from server.nl2sql.algorithm.orchestrator import Nl2sqlOrchestrator

    with pytest.raises(Nl2sqlError, match="问题不能为空"):
        asyncio.run(Nl2sqlOrchestrator().ask("   ", dataset_id=dataset_id))
    with pytest.raises(Nl2sqlError, match="请选择至少一个数据集"):
        asyncio.run(Nl2sqlOrchestrator().ask(QUESTION))
    with pytest.raises(Nl2sqlError, match="数据集不存在"):
        asyncio.run(Nl2sqlOrchestrator().ask(QUESTION, dataset_id="no-such-id"))


def test_ask_route_validation_error(monkeypatch, tmp_path) -> None:
    """路由层：空问题 422；未认证 401。"""
    client = _client(monkeypatch, tmp_path)
    assert client.post("/nl2sql/ask", json={"question": "x"}).status_code == 401
    admin = _admin(client)
    assert client.post("/nl2sql/ask", headers=admin, json={"question": ""}).status_code == 422
