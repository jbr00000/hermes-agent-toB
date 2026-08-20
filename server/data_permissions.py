"""Table-level data permissions for db_query (④ 表级数据权限).

Per-role whitelists are declared in deployment.yaml:

    data_permissions:
      enabled: true
      roles:
        user: [orders, customers]       # user 角色只能查这两张表
        # admin 未列出 → 不限制

Enforcement point: tools/db_query.py checks every statement through
``check_sql_allowed`` before executing. Table names are compared lowercase,
both bare (``orders``) and qualified (``sales.orders``) forms match a
whitelist entry.

Fail-closed: when a role IS restricted and the SQL cannot be parsed, the
statement is rejected (a parser gap must not become a permission bypass).

Known boundary (documented for reviewers): a full-permission user could in
theory run code in the sandbox with the shared read-only DB credentials and
bypass this check; the chat/plan paths (db+web toolsets) are sealed.
"""
from __future__ import annotations

import hashlib
import logging

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


def _config():
    from server.deployment_config import load_deployment_config

    return load_deployment_config().data_permissions


def is_enabled() -> bool:
    """数据权限开关是否打开（tools/db_query 在基础设施异常时据此 fail-closed）。"""
    return bool(_config().enabled)


def allowed_tables_for_role(role: str) -> list[str] | None:
    """返回该角色的表白名单；None 表示不限制（功能关闭或角色未列出）。"""
    config = _config()
    if not config.enabled:
        return None
    tables = config.roles.get((role or "").strip().lower())
    return tables  # 未列出的角色 → None（不限制）；列出但为空 → 全部禁止


def _referenced_tables(sql: str) -> list[set[str]] | None:
    """提取 SQL 引用的表：每张表给出一组可接受的匹配形态（小写的裸名与
    ``db.table`` 限定名）。解析失败返回 None。"""
    try:
        statement = sqlglot.parse_one(sql)
    except Exception:
        return None
    if statement is None:
        return None
    # CTE 别名不是真实表（WITH recent AS (...) 里的 recent）。
    cte_aliases = {
        (cte.alias or "").strip().lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias
    }
    tables: list[set[str]] = []
    for table in statement.find_all(exp.Table):
        name = (table.name or "").strip().lower()
        if not name:
            continue
        db = (table.db or "").strip().lower()
        if not db and name in cte_aliases:
            continue
        forms = {name}
        if db:
            forms.add(f"{db}.{name}")
        tables.append(forms)
    return tables


def _sql_fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]


def check_sql_allowed(sql: str, role: str) -> str | None:
    """返回 None 表示放行；否则返回拒绝原因（面向用户，不含 SQL 原文）。"""
    allowed = allowed_tables_for_role(role)
    if allowed is None:
        return None
    allowed_set = set(allowed)
    referenced = _referenced_tables(sql)
    if referenced is None:
        logger.warning(
            "data_permissions: unparseable SQL rejected for restricted role "
            "(fingerprint=%s)",
            _sql_fingerprint(sql),
        )
        return "当前角色受数据权限限制，且该 SQL 无法解析，已被拦截"
    denied = sorted(
        min(forms) for forms in referenced if not forms & allowed_set
    )
    if denied:
        return f"当前角色无权访问表：{', '.join(denied)}"
    return None
