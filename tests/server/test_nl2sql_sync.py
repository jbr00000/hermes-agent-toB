"""API tests: /nl2sql 三端同步（server/nl2sql/sync.py）—— 幂等性、双端写入契约、历史落库。

ES/Milvus/embedding 均 monkeypatch 为记录型假客户端（CI 无检索端）；
断言的是同步不变量：delete-then-write 顺序、ES/Milvus 文档 id 一致（RRF 对齐）、
dataset_id 作用域、DDL 字段切分口径、分段成败落 sync_history。
"""
from __future__ import annotations

from typing import Any

from test_nl2sql_orchestrator import _admin, _client  # tests/server 非 package，按 prepend 模式直导


class _FakeEsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def create_index(self, *, index: str, mappings: dict) -> None:
        self.calls.append(("create_index", index))

    def delete_by_term(self, index_name: str, field_name: str, field_value: Any) -> dict:
        self.calls.append(("delete", index_name, field_name, str(field_value)))
        return {"deleted": 0}

    def bulk_insert(self, index_name: str, documents: list[dict], *, id_field: str | None = None) -> dict:
        self.calls.append(("bulk", index_name, id_field, documents))
        return {"success": len(documents), "failed": 0, "total": len(documents)}


class _FakeMilvusCollection:
    def __init__(self, sink: "_FakeMilvusClient", name: str) -> None:
        self._sink = sink
        self._name = name

    def load(self) -> None:
        pass

    def delete(self, expr: str) -> None:
        self._sink.calls.append(("delete", self._name, expr))

    def flush(self) -> None:
        pass

    def insert(self, batch: list[dict]) -> Any:
        self._sink.calls.append(("insert", self._name, batch))
        return type("R", (), {"primary_keys": [row["id"] for row in batch]})()


class _FakeMilvusClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def has_collection(self, collection_name: str) -> bool:
        return True

    def create_collection(self, collection_name: str, fields: list, field_names: Any, index_params: Any) -> None:
        self.calls.append(("create_collection", collection_name))

    def get_collection(self, collection_name: str) -> _FakeMilvusCollection:
        return _FakeMilvusCollection(self, collection_name)

    def batch_insert_data(self, collection_name: str, data: list[dict], batch_size: int = 1000) -> int:
        for start in range(0, len(data), batch_size):
            _FakeMilvusCollection(self, collection_name).insert(data[start:start + batch_size])
        return len(data)


class _FakeEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.embedded: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding 端点不可达")
        self.embedded.extend(texts)
        return [[0.01] * 8 for _ in texts]


def _seed_full_dataset(client) -> str:
    """建数据源+数据集+五类元数据（含一张停用表 + 一张带注释 DDL 的启用表）。"""
    admin = _admin(client)
    response = client.post("/nl2sql/datasources", headers=admin, json={
        "name": "基金问数库", "db_type": "mysql", "host": "127.0.0.1", "port": 13306,
        "database_name": "nl2sql_fund", "username": "reader", "password": "secret-pw",
    })
    datasource = response.json()["datasource"]
    response = client.post("/nl2sql/datasets", headers=admin, json={
        "name": "基金数据集", "datasource_id": datasource["id"],
        "description": "", "system_prompt": "",
    })
    dataset = response.json()["dataset"]
    dataset_id = dataset["id"]

    ddl = (
        "CREATE TABLE mf_fundarchives (\n"
        "  InnerCode int COMMENT '基金内部代码',\n"
        "  FundType varchar(32) COMMENT '基金类别',\n"
        "  SecretCol varchar(32),\n"  # 无注释 → 不进字段索引
        "  PRIMARY KEY (InnerCode)\n"
        ");"
    )
    seeds = {
        "tables": [
            {"table_name": "mf_fundarchives", "ddl_content": ddl, "description": "基金档案", "enabled": True, "provider": "MANUAL"},
            {"table_name": "mf_disabled", "ddl_content": ddl, "description": "停用表", "enabled": False, "provider": "MANUAL"},
        ],
        "terms": [{"terminology": "收益为正", "terminology_explain": "今年以来收益率 > 0", "synonyms": "正收益", "provider": "MANUAL"}],
        "metrics": [{"index_name": "fund_count", "index_display_name": "基金数量", "calculate_method": "COUNT(DISTINCT InnerCode)", "provider": "MANUAL"}],
        "dimensions": [{"dimension_name": "mf_fundarchives.FundType", "dimension_display_name": "基金类别",
                        "db_data_key": "1", "db_data_value": "股票型", "provider": "MANUAL"}],
        "examples": [{"question": "各基金类别有多少只基金？", "question_sql": "SELECT ... GROUP BY FundType", "provider": "MANUAL"}],
    }
    for kind, items in seeds.items():
        for fields in items:
            response = client.put(f"/nl2sql/datasets/{dataset_id}/meta/{kind}", headers=admin, json={"fields": fields})
            assert response.status_code == 200, response.text
    return dataset_id


def _patch_sync_infra(monkeypatch, *, embedder_fail: bool = False):
    from server.nl2sql import sync

    es = _FakeEsClient()
    milvus = _FakeMilvusClient()
    embedder = _FakeEmbedder(fail=embedder_fail)
    monkeypatch.setattr(sync, "get_es_client", lambda config=None: es)
    monkeypatch.setattr(sync, "get_milvus_client", lambda config=None: milvus)
    monkeypatch.setattr(sync, "get_embedder", lambda config=None: embedder)
    return es, milvus, embedder


def test_sync_dataset_delete_then_write_and_id_alignment(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_full_dataset(client)
    es, milvus, embedder = _patch_sync_infra(monkeypatch)

    from server.nl2sql import sync

    record = sync.sync_dataset(dataset_id)

    assert record["overall_status"] == "success"
    for key in sync.SEGMENT_KEYS:
        assert record[f"{key}_status"] == "success", key

    # 五段都先 delete 后 write（幂等 delete-then-write），且删除按 dataset_id 作用域
    for index_name in sync.INDEX_NAMES.values():
        es_delete_at = next(i for i, c in enumerate(es.calls) if c[0] == "delete" and c[1] == index_name)
        es_bulk_at = next(i for i, c in enumerate(es.calls) if c[0] == "bulk" and c[1] == index_name)
        assert es_delete_at < es_bulk_at, index_name
        assert es.calls[es_delete_at][2] == "dataset_id" and es.calls[es_delete_at][3] == dataset_id
        milvus_delete = next(c for c in milvus.calls if c[0] == "delete" and c[1] == index_name)
        assert f'dataset_id == "{dataset_id}"' in milvus_delete[2]

    # ES _id 与 Milvus 主键一致（RRF 融合靠 id 跨引擎对齐）；文档都带 dataset_id
    for index_name in sync.INDEX_NAMES.values():
        es_docs = next(c[3] for c in es.calls if c[0] == "bulk" and c[1] == index_name)
        milvus_rows = [row for c in milvus.calls if c[0] == "insert" and c[1] == index_name for row in c[2]]
        assert es_docs and milvus_rows, index_name
        assert {doc["id"] for doc in es_docs} == {row["id"] for row in milvus_rows}, index_name
        assert all(doc["dataset_id"] == dataset_id for doc in es_docs)
        assert all(isinstance(row["vector"], list) for row in milvus_rows)

    # DDL 段：只切带 COMMENT 的列（2 条），停用表不切
    ddl_docs = next(c[3] for c in es.calls if c[0] == "bulk" and c[1] == sync.INDEX_NAMES["DDL_CHUNK"])
    assert {doc["field_name"] for doc in ddl_docs} == {"InnerCode", "FundType"}
    assert all(doc["table_name"] == "mf_fundarchives" for doc in ddl_docs)
    assert record["ddl_message"] == "2 条"

    # 幂等重跑：同一份元数据产出同一批 id（delete-then-write 不留重复）
    es_calls_before = len(es.calls)
    record2 = sync.sync_dataset(dataset_id)
    assert record2["overall_status"] == "success"
    ddl_docs2 = next(c[3] for c in es.calls[es_calls_before:] if c[0] == "bulk" and c[1] == sync.INDEX_NAMES["DDL_CHUNK"])
    assert {doc["id"] for doc in ddl_docs2} == {doc["id"] for doc in ddl_docs}

    # 术语段 embedding 文本 = 术语 + 近义词（检索 search_fields 同口径）
    assert "收益为正 正收益" in embedder.embedded


def test_sync_dataset_segment_failure_isolated(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_full_dataset(client)
    _patch_sync_infra(monkeypatch, embedder_fail=True)

    from server.nl2sql import sync

    record = sync.sync_dataset(dataset_id)
    assert record["overall_status"] == "failed"
    assert all(record[f"{key}_status"] == "failed" for key in sync.SEGMENT_KEYS)
    assert "embedding" in (record["overall_message"] or "") or record["overall_message"]

    # 历史可列表读回（图4 弹窗数据源）
    admin = _admin(client)
    history = client.get(f"/nl2sql/datasets/{dataset_id}/sync-history", headers=admin).json()["history"]
    assert [row["id"] for row in history][:1] == [history[0]["id"]]
    assert history[0]["overall_status"] == "failed"


def test_sync_route_accepted_and_conflict(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    dataset_id = _seed_full_dataset(client)
    admin = _admin(client)

    from server.nl2sql import sync as sync_module

    calls: list[str] = []

    def fake_start_sync(ds_id: str, **kwargs):
        calls.append(ds_id)
        return {"id": "rec-1", "dataset_id": ds_id, "overall_status": "running"}

    monkeypatch.setattr(sync_module, "start_sync", fake_start_sync)

    response = client.post(f"/nl2sql/datasets/{dataset_id}/sync", headers=admin)
    assert response.status_code == 202, response.text
    assert response.json()["record"]["overall_status"] == "running"
    assert calls == [dataset_id]

    # start_sync 返回 None（已在跑）→ 409
    monkeypatch.setattr(sync_module, "start_sync", lambda ds_id, **kwargs: None)
    response = client.post(f"/nl2sql/datasets/{dataset_id}/sync", headers=admin)
    assert response.status_code == 409

    # 未认证 / 非管理员不可触发
    assert client.post(f"/nl2sql/datasets/{dataset_id}/sync").status_code == 401
