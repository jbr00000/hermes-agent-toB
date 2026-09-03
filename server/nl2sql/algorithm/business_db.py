"""业务库执行器 —— 对齐 lone-ai ``core/mysql.py`` 的查询语义。

不搬 MysqlClient 的连接管理（lone-ai 是常驻连接 + create_database，那是它
平台侧的职责）；问数每次执行开短连接，用完即关。数据源密码来自
``nl2sql_datasource.password_enc``（Fernet 密文，仅在本层解密进内存）。
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal
from typing import Any

import pymysql
import pymysql.cursors

from server.nl2sql.crypto import decrypt_password

from . import Nl2sqlError

# lone-ai SQLExecutionService 的截断口径：结果 JSON 超过该长度时截到 30 条
MAX_RESULT_JSON_CHARS = 30000
TRUNCATED_ROWS = 30


class DecimalJSONEncoder(json.JSONEncoder):
    """Decimal 与日期时间的 JSON 序列化（与 lone-ai 一致）。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime.datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, datetime.date):
            return o.strftime("%Y-%m-%d")
        if isinstance(o, datetime.time):
            return o.strftime("%H:%M:%S")
        return super().default(o)


def json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 pymysql 行里的 Decimal/datetime 转成 JSON 原生类型（往返一次保证干净）。"""
    return json.loads(json.dumps(rows, cls=DecimalJSONEncoder, ensure_ascii=False))


def exec_query(datasource: dict[str, Any], sql: str, *, timeout: int = 30) -> list[dict[str, Any]]:
    """在数据集对应的业务库上执行只读 SQL，返回 JSON 安全的 dict 行。

    ``datasource`` 是 store.get_datasource 的返回（含 password_enc 密文）。
    只读由两层保证：SQLValidator 拦写语句 + 业务库账号本身应是只读 GRANT。
    """
    if datasource.get("db_type", "mysql") != "mysql":
        raise Nl2sqlError(f"问数暂只支持 MySQL 数据源，当前: {datasource.get('db_type')}")
    password = decrypt_password(datasource.get("password_enc"))
    try:
        connection = pymysql.connect(
            host=datasource["host"],
            port=int(datasource["port"]),
            user=datasource["username"],
            password=password,
            database=datasource["database_name"],
            charset="utf8mb4",
            connect_timeout=10,
            read_timeout=timeout,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.Error as exc:
        raise Nl2sqlError(f"连接业务库失败: {exc}") from exc
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return json_safe_rows(list(rows))
    except pymysql.Error as exc:
        raise Nl2sqlError(f"SQL 执行失败: {exc}") from exc
    finally:
        connection.close()
