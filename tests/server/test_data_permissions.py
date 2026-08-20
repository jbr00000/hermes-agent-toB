"""表级数据权限（server/data_permissions.py + tools/db_query.py 接线）。

单元层：check_sql_allowed 对各 SQL 形态的放行/拦截；
集成层：_handle 走真实 storage + 临时 sqlite 业务库，拦截时审计只留指纹。
"""
from __future__ import annotations

import json
import sqlite3

import pytest


def _write_config(tmp_path, body: str) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(body.strip(), encoding="utf-8")
    return path


@pytest.fixture
def dp_env(monkeypatch, tmp_path):
    """部署配置工厂：data_permissions enabled + roles 由参数定制。"""

    def _make(roles_yaml: str | None) -> None:
        if roles_yaml is None:
            body = "data_permissions:\n  enabled: false\n"
        else:
            body = f"data_permissions:\n  enabled: true\n  roles:\n{roles_yaml}\n"
        monkeypatch.setenv(
            "HERMES_DEPLOYMENT_CONFIG", str(_write_config(tmp_path, body))
        )

    return _make


def test_disabled_feature_allows_everything(dp_env) -> None:
    dp_env(None)

    from server.data_permissions import allowed_tables_for_role, check_sql_allowed

    assert allowed_tables_for_role("user") is None
    assert check_sql_allowed("SELECT * FROM salaries", "user") is None


def test_unlisted_role_is_unrestricted(dp_env) -> None:
    dp_env("    user:\n      - orders\n")

    from server.data_permissions import allowed_tables_for_role, check_sql_allowed

    assert allowed_tables_for_role("admin") is None
    assert check_sql_allowed("SELECT * FROM salaries", "admin") is None
    # 列出的角色则受限。
    assert allowed_tables_for_role("user") == ["orders"]


def test_allowed_and_denied_tables(dp_env) -> None:
    dp_env("    user:\n      - orders\n      - customers\n")

    from server.data_permissions import check_sql_allowed

    assert check_sql_allowed("SELECT * FROM orders", "user") is None
    # 大小写不敏感。
    assert check_sql_allowed("SELECT * FROM ORDERS", "user") is None
    reason = check_sql_allowed("SELECT * FROM salaries", "user")
    assert reason is not None and "salaries" in reason


def test_qualified_table_names(dp_env) -> None:
    dp_env("    user:\n      - sales.orders\n")

    from server.data_permissions import check_sql_allowed

    assert check_sql_allowed("SELECT * FROM sales.orders", "user") is None
    # 同裸名、不同库限定 → 拒绝。
    reason = check_sql_allowed("SELECT * FROM other.orders", "user")
    assert reason is not None


def test_bare_whitelist_entry_matches_qualified_query(dp_env) -> None:
    dp_env("    user:\n      - orders\n")

    from server.data_permissions import check_sql_allowed

    assert check_sql_allowed("SELECT * FROM sales.orders", "user") is None


def test_join_subquery_and_cte_all_checked(dp_env) -> None:
    dp_env("    user:\n      - orders\n      - customers\n")

    from server.data_permissions import check_sql_allowed

    assert (
        check_sql_allowed(
            "SELECT * FROM orders JOIN customers ON orders.cid = customers.id", "user"
        )
        is None
    )
    # JOIN 中一张表越权 → 整条拦截。
    assert (
        check_sql_allowed(
            "SELECT * FROM orders JOIN salaries ON orders.cid = salaries.id", "user"
        )
        is not None
    )
    # 子查询。
    assert (
        check_sql_allowed(
            "SELECT * FROM orders WHERE cid IN (SELECT id FROM customers)", "user"
        )
        is None
    )
    assert (
        check_sql_allowed(
            "SELECT * FROM orders WHERE cid IN (SELECT id FROM salaries)", "user"
        )
        is not None
    )
    # CTE。
    assert (
        check_sql_allowed(
            "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent", "user"
        )
        is None
    )
    assert (
        check_sql_allowed(
            "WITH s AS (SELECT * FROM salaries) SELECT * FROM s", "user"
        )
        is not None
    )


def test_empty_whitelist_denies_everything(dp_env) -> None:
    dp_env("    user: []\n")

    from server.data_permissions import check_sql_allowed

    assert check_sql_allowed("SELECT * FROM orders", "user") is not None


def test_unparseable_sql_fail_closed_for_restricted_role(dp_env) -> None:
    dp_env("    user:\n      - orders\n")

    from server.data_permissions import check_sql_allowed

    garbage = "THIS IS ((( NOT SQL"
    reason = check_sql_allowed(garbage, "user")
    assert reason is not None
    assert garbage not in reason  # 拒绝原因不回显 SQL 原文
    # 不受限角色不受解析失败影响。
    assert check_sql_allowed(garbage, "admin") is None


# ---------------------------------------------------------------------------
# 集成：tools/db_query._handle 真实走 storage + 业务库 + 审计
# ---------------------------------------------------------------------------


def _init_storage(monkeypatch, tmp_path):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)

    from server.storage import init_storage, reset_storage_for_tests

    reset_storage_for_tests()
    init_storage()

    from server.storage import get_repository

    return get_repository()


def _business_db(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "business.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
    con.execute("INSERT INTO orders (amount) VALUES (9.9)")
    con.execute("CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)")
    con.commit()
    con.close()
    monkeypatch.setenv("HERMES_DB_URL", f"sqlite:///{db_path.as_posix()}")


def test_handle_allows_whitelisted_query(monkeypatch, tmp_path, dp_env) -> None:
    dp_env("    user:\n      - orders\n")
    repository = _init_storage(monkeypatch, tmp_path)
    _business_db(monkeypatch, tmp_path)
    user = repository.create_user("analyst", "hash", "user")

    from tools.db_query import _handle

    result = json.loads(
        _handle({"sql": "SELECT amount FROM orders"}, user_id=user["id"], session_id="s1")
    )
    assert result["row_count"] == 1


def test_handle_blocks_denied_table_and_audits_fingerprint_only(
    monkeypatch, tmp_path, dp_env
) -> None:
    dp_env("    user:\n      - orders\n")
    repository = _init_storage(monkeypatch, tmp_path)
    _business_db(monkeypatch, tmp_path)
    user = repository.create_user("analyst", "hash", "user")

    from tools.db_query import _handle

    sql = "SELECT amount FROM salaries"
    result = json.loads(_handle({"sql": sql}, user_id=user["id"], session_id="s1"))
    assert "error" in result and "salaries" in result["error"]

    from server.storage import get_repository

    events = get_repository().list_audit_events()
    blocked = [event for event in events if event["status"] == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["metadata"]["tool_name"] == "db_query"
    args_summary = blocked[0]["metadata"]["args"]
    assert "sql_fingerprint" in args_summary
    assert sql not in json.dumps(blocked[0], ensure_ascii=False)


def test_handle_without_user_id_skips_check(monkeypatch, tmp_path, dp_env) -> None:
    dp_env("    user:\n      - orders\n")
    _init_storage(monkeypatch, tmp_path)
    _business_db(monkeypatch, tmp_path)

    from tools.db_query import _handle

    # CLI/admin 路径没有 user_id → 不做数据权限检查。
    result = json.loads(_handle({"sql": "SELECT amount FROM salaries"}))
    assert result["row_count"] == 0


# ---------------------------------------------------------------------------
# fail-open/fail-closed：基础设施异常时按开关决定
# ---------------------------------------------------------------------------


def _break_storage(monkeypatch) -> None:
    import server.storage

    def _boom():
        raise RuntimeError("storage down")

    monkeypatch.setattr(server.storage, "get_repository", _boom)


def test_storage_error_with_feature_enabled_fails_closed(
    monkeypatch, tmp_path, dp_env, caplog
) -> None:
    dp_env("    user:\n      - orders\n")
    _init_storage(monkeypatch, tmp_path)
    _business_db(monkeypatch, tmp_path)
    _break_storage(monkeypatch)

    from tools.db_query import _check_data_permissions

    result = _check_data_permissions("SELECT * FROM orders", {"user_id": "u1"})
    assert result is not None
    assert "error" in json.loads(result)
    assert "data_permissions check failed" in caplog.text


def test_storage_error_with_feature_disabled_allows_and_logs(
    monkeypatch, tmp_path, dp_env, caplog
) -> None:
    dp_env(None)  # enabled: false
    _init_storage(monkeypatch, tmp_path)
    _business_db(monkeypatch, tmp_path)
    _break_storage(monkeypatch)

    from tools.db_query import _check_data_permissions

    with caplog.at_level("ERROR", logger="tools.db_query"):
        assert _check_data_permissions("SELECT * FROM orders", {"user_id": "u1"}) is None
    assert "data_permissions check failed" in caplog.text
