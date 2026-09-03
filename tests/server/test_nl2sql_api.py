"""API tests: /nl2sql routes — 数据源/数据集 CRUD、六类元数据、Excel 导入导出闭环。

断言的是契约不变量（脱敏、级联、幂等、权限边界），不是具体字段快照。
"""
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


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "correct-horse-battery-staple")


def _make_datasource(client: TestClient, headers: dict[str, str], name: str = "基金问数库") -> dict:
    response = client.post("/nl2sql/datasources", headers=headers, json={
        "name": name, "db_type": "mysql", "host": "127.0.0.1", "port": 13306,
        "database_name": "nl2sql_fund", "username": "reader", "password": "secret-pw",
    })
    assert response.status_code == 201, response.text
    return response.json()["datasource"]


def _make_dataset(client: TestClient, headers: dict[str, str], datasource_id: str, name: str = "基金数据集") -> dict:
    response = client.post("/nl2sql/datasets", headers=headers, json={
        "name": name, "datasource_id": datasource_id,
        "description": "公募基金问数", "system_prompt": "你是基金领域问数助手",
    })
    assert response.status_code == 201, response.text
    return response.json()["dataset"]


def test_unauthenticated_returns_401(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.get("/nl2sql/datasources").status_code == 401
    assert client.get("/nl2sql/datasets").status_code == 401


def test_datasource_password_never_returned(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)

    created = _make_datasource(client, admin)
    assert created["has_password"] is True
    assert "password" not in created and "password_enc" not in created

    listed = client.get("/nl2sql/datasources", headers=admin).json()["datasources"]
    assert listed[0]["has_password"] is True
    assert all("password_enc" not in row for row in listed)

    # 编辑留空密码 = 不修改；has_password 仍为 true，且连接参数变更重置测试状态
    response = client.put(
        f"/nl2sql/datasources/{created['id']}", headers=admin,
        json={
            "name": "基金问数库", "db_type": "mysql", "host": "127.0.0.2", "port": 13306,
            "database_name": "nl2sql_fund", "username": "reader",
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()["datasource"]
    assert updated["has_password"] is True
    assert updated["last_test_status"] is None


def test_datasource_name_unique_and_delete_guard(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)

    first = _make_datasource(client, admin)
    duplicate = client.post("/nl2sql/datasources", headers=admin, json={
        "name": "基金问数库", "db_type": "mysql", "host": "h", "port": 3306,
        "database_name": "db", "username": "u",
    })
    assert duplicate.status_code == 409

    dataset = _make_dataset(client, admin, first["id"])
    blocked = client.delete(f"/nl2sql/datasources/{first['id']}", headers=admin)
    assert blocked.status_code == 409

    assert client.delete(f"/nl2sql/datasets/{dataset['id']}", headers=admin).status_code == 200
    assert client.delete(f"/nl2sql/datasources/{first['id']}", headers=admin).status_code == 200


def test_dataset_delete_cascades_meta(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)
    datasource = _make_datasource(client, admin)
    dataset = _make_dataset(client, admin, datasource["id"])

    response = client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/tables", headers=admin,
        json={"fields": {"table_name": "mf_fundarchives", "ddl_content": "CREATE TABLE t (id INT)"}},
    )
    assert response.status_code == 200, response.text
    bundle = client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=admin).json()["meta"]
    assert len(bundle["tables"]) == 1
    assert bundle["tables"][0]["enabled"] is True  # 新增默认启用

    listed = client.get("/nl2sql/datasets", headers=admin).json()["datasets"]
    assert listed[0]["ddl_count"] == 1 and listed[0]["rule_count"] == 0

    assert client.delete(f"/nl2sql/datasets/{dataset['id']}", headers=admin).status_code == 200
    orphan = client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=admin)
    assert orphan.status_code == 404


def test_meta_upsert_all_kinds_and_unknown_kind_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)
    dataset = _make_dataset(client, admin, _make_datasource(client, admin)["id"])

    seeds = {
        "tables": {"table_name": "t1", "ddl_content": "CREATE TABLE t1 (id INT)"},
        "terms": {"terminology": "正收益", "terminology_explain": "return > 0"},
        "metrics": {"index_name": "基金数", "calculate_method": "COUNT(*)"},
        "dimensions": {"dimension_name": "t1.kind", "db_data_key": "01", "db_data_value": "股票型"},
        "foreignKeys": {"source_table": "t1", "source_column": "id", "target_table": "t2", "target_column": "id"},
        "examples": {"question": "有多少只基金", "question_sql": "SELECT COUNT(*) FROM t1"},
    }
    for kind, fields in seeds.items():
        response = client.put(f"/nl2sql/datasets/{dataset['id']}/meta/{kind}", headers=admin, json={"fields": fields})
        assert response.status_code == 200, (kind, response.text)

    bundle = client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=admin).json()["meta"]
    for kind in seeds:
        assert len(bundle[kind]) == 1
        # 编辑：同 id 更新而不是新增
        item = bundle[kind][0]
        response = client.put(
            f"/nl2sql/datasets/{dataset['id']}/meta/{kind}", headers=admin,
            json={"id": item["id"], "fields": {"remark": "已人工复核"}},
        )
        assert response.status_code == 200, (kind, response.text)
        assert client.delete(
            f"/nl2sql/datasets/{dataset['id']}/meta/{kind}/{item['id']}", headers=admin,
        ).status_code == 200

    assert client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=admin).json()["meta"]["tables"] == []
    assert client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/nope", headers=admin, json={"fields": {}},
    ).status_code == 404


def test_excel_template_export_import_roundtrip(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)
    dataset = _make_dataset(client, admin, _make_datasource(client, admin)["id"])
    client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/tables", headers=admin,
        json={"fields": {"table_name": "mf_fundarchives", "ddl_content": "CREATE TABLE x (id INT)", "description": "公募基金概况"}},
    )

    template = client.get(f"/nl2sql/datasets/{dataset['id']}/meta/template", headers=admin)
    assert template.status_code == 200
    assert template.content[:2] == b"PK"  # xlsx zip 头

    exported = client.get(f"/nl2sql/datasets/{dataset['id']}/meta/export", headers=admin)
    assert exported.status_code == 200 and exported.content[:2] == b"PK"

    # 导出文件原样回灌 → 全部识别为 duplicate，confirm 幂等（0 新增 0 更新）
    preview = client.post(
        f"/nl2sql/datasets/{dataset['id']}/meta/import/preview", headers=admin,
        files={"file": ("meta.xlsx", exported.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["type_summaries"]["tables"]["duplicate"] == 1
    assert body["type_summaries"]["tables"]["create"] == 0

    confirmed = client.post(
        f"/nl2sql/datasets/{dataset['id']}/meta/import/confirm", headers=admin,
        json={"preview_id": body["preview_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {"created": 0, "updated": 0}
    # preview 一次性消费：重复 confirm 视为过期
    assert client.post(
        f"/nl2sql/datasets/{dataset['id']}/meta/import/confirm", headers=admin,
        json={"preview_id": body["preview_id"]},
    ).status_code == 404


def test_import_rejects_non_xlsx_and_clear(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)
    dataset = _make_dataset(client, admin, _make_datasource(client, admin)["id"])

    bad = client.post(
        f"/nl2sql/datasets/{dataset['id']}/meta/import/preview", headers=admin,
        files={"file": ("meta.csv", b"a,b", "text/csv")},
    )
    assert bad.status_code == 400

    client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/terms", headers=admin,
        json={"fields": {"terminology": "t"}},
    )
    assert client.post(f"/nl2sql/datasets/{dataset['id']}/meta/clear", headers=admin).status_code == 200
    bundle = client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=admin).json()["meta"]
    assert all(len(items) == 0 for items in bundle.values())

    history = client.get(f"/nl2sql/datasets/{dataset['id']}/sync-history", headers=admin)
    assert history.status_code == 200 and history.json()["history"] == []


def test_mutations_require_admin(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    admin = _admin(client)
    datasource = _make_datasource(client, admin)
    dataset = _make_dataset(client, admin, datasource["id"])

    response = client.post("/users", headers=admin, json={"username": "regular", "password": "password-123"})
    assert response.status_code == 200, response.text
    user = _login(client, "regular", "password-123")
    changed = client.post(
        "/auth/change-password", headers=user,
        json={"old_password": "password-123", "new_password": "password-456"},
    )
    user = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    # 读：登录即可；写：admin-only
    assert client.get("/nl2sql/datasets", headers=user).status_code == 200
    assert client.get(f"/nl2sql/datasets/{dataset['id']}/meta", headers=user).status_code == 200
    assert client.post("/nl2sql/datasources", headers=user, json={
        "name": "x", "db_type": "mysql", "host": "h", "port": 1,
        "database_name": "d", "username": "u",
    }).status_code == 403
    assert client.put(
        f"/nl2sql/datasets/{dataset['id']}/meta/terms", headers=user,
        json={"fields": {"terminology": "t"}},
    ).status_code == 403
