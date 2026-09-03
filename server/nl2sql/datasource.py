"""业务库直连助手：测试连接 + 抓取表结构 DDL。

连接信息来自 nl2sql_datasource 行（密码内存中解密，不落日志）。
当前仅支持 MySQL（PyMySQL 已随依赖 pin）；PostgreSQL 数据源可建可存，
但测试/抓 DDL 在缺少驱动时返回明确错误——算法端同样只走 mysql/postgresql
两类连接配置，后续需要时补 psycopg 依赖即可。
"""
from __future__ import annotations

import time
from typing import Any

from server.nl2sql.crypto import decrypt_password


class DatasourceError(Exception):
    """业务库连接/查询失败（message 可直接回前端展示，不含口令）。"""


def _connect(datasource: dict[str, Any]):
    db_type = datasource["db_type"]
    if db_type != "mysql":
        raise DatasourceError(f"暂不支持 {db_type} 类型数据源的在线操作（当前仅实现 mysql）")
    import pymysql  # 延迟导入：仅测试连接/抓 DDL 时才需要

    return pymysql.connect(
        host=datasource["host"],
        port=int(datasource["port"]),
        user=datasource["username"],
        password=decrypt_password(datasource.get("password_enc")),
        database=datasource["database_name"],
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=30,
    )


def test_connection(datasource: dict[str, Any]) -> dict[str, Any]:
    """SELECT 1 探活。返回 {success, message, latency_ms}。"""
    started = time.monotonic()
    try:
        conn = _connect(datasource)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
    except DatasourceError as exc:
        return {"success": False, "message": str(exc), "latency_ms": None}
    except Exception as exc:  # pymysql OperationalError 等：message 不含口令
        return {"success": False, "message": str(exc)[:300], "latency_ms": None}
    return {
        "success": True,
        "message": "连接成功",
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def fetch_table_ddls(datasource: dict[str, Any]) -> list[dict[str, str]]:
    """从业务库抓全部基表的 DDL + 表注释（information_schema + SHOW CREATE TABLE）。"""
    conn = _connect(datasource)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, table_comment FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name"
            , (datasource["database_name"],))
            tables = [(row[0], row[1] or "") for row in cur.fetchall()]
            results: list[dict[str, str]] = []
            for table_name, comment in tables:
                cur.execute(f"SHOW CREATE TABLE `{table_name}`")
                row = cur.fetchone()
                if row is None:
                    continue
                results.append({
                    "table_name": table_name,
                    "ddl_content": row[1],
                    "description": comment,
                })
            return results
    except DatasourceError:
        raise
    except Exception as exc:
        raise DatasourceError(str(exc)[:300]) from exc
    finally:
        conn.close()
