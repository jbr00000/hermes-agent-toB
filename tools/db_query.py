"""Read-only database query tool — the agent's window onto the customer's data.

Connects via ``HERMES_DB_URL`` (``sqlite:///path``, ``postgresql://...``,
``mysql://...``). Read-only is enforced at the DATABASE GRANT layer (the
connecting user has SELECT only) — NOT in this tool — because the code
sandbox (Step 3.3) connects with the SAME credentials and any tool-level
restriction would be bypassable from generated code. See 改造计划.md decision 6.

SQLite is supported via the stdlib (no extra deps). Other backends need
SQLAlchemy + the relevant driver, lazy-imported on first use (the customer's
deployment installs them).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any

from tools.registry import registry

_MAX_ROWS = 200
_DEFAULT_TIMEOUT_SECONDS = 30.0
_READ_ONLY_START = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE"}
_DANGEROUS_SQL_TOKENS = {
    "ALTER",
    "ATTACH",
    "BEGIN",
    "CALL",
    "COMMIT",
    "COPY",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "INSERT",
    "LOAD",
    "MERGE",
    "PRAGMA",
    "REINDEX",
    "REPLACE",
    "REVOKE",
    "ROLLBACK",
    "SET",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}
_DANGEROUS_SQL_PATTERNS = (
    re.compile(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b", re.IGNORECASE),
)


def _strip_sql_literals_and_comments(sql: str) -> str:
    """Return SQL with strings/comments blanked so keyword checks are stable."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append("  ")
                    i += 2
                    continue
                quote = None
            out.append(" ")
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            continue
        if ch == "-" and nxt == "-":
            while i < len(sql) and sql[i] not in "\r\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < len(sql):
                if sql[i] == "*" and i + 1 < len(sql) and sql[i + 1] == "/":
                    out.append("  ")
                    i += 2
                    break
                out.append(" ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _validate_read_only_sql(sql: str) -> None:
    scrubbed = _strip_sql_literals_and_comments(sql or "")
    statement = scrubbed.strip()
    if not statement:
        raise ValueError("db_query requires a non-empty read-only SQL statement")
    if statement.endswith(";"):
        if ";" in statement[:-1]:
            raise ValueError("db_query accepts a single read-only SQL statement")
    elif ";" in statement:
        raise ValueError("db_query accepts a single read-only SQL statement")

    tokens = [token.upper() for token in re.findall(r"[A-Za-z_]+", statement)]
    if not tokens or tokens[0] not in _READ_ONLY_START:
        raise ValueError("db_query only accepts read-only SELECT/WITH/EXPLAIN/SHOW/DESCRIBE statements")
    dangerous = sorted(set(tokens) & _DANGEROUS_SQL_TOKENS)
    if dangerous:
        raise ValueError(f"db_query rejected non read-only SQL token: {dangerous[0]}")
    for pattern in _DANGEROUS_SQL_PATTERNS:
        if pattern.search(statement):
            raise ValueError("db_query rejected non read-only SQL output directive")


def _deployment_database_config():
    try:
        from server.deployment_config import load_deployment_config

        return load_deployment_config().database
    except Exception:
        return None


def _database_url_env_name() -> str:
    deployment_database = _deployment_database_config()
    if deployment_database is not None:
        return str(deployment_database.url_env or "HERMES_DB_URL")
    return "HERMES_DB_URL"


def _configured_max_rows() -> int:
    env_value = os.environ.get("HERMES_DB_QUERY_MAX_ROWS")
    if env_value:
        try:
            parsed = int(env_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    deployment_database = _deployment_database_config()
    if deployment_database is not None:
        return int(deployment_database.max_rows)
    return _MAX_ROWS


def _configured_timeout_seconds() -> float:
    env_value = os.environ.get("HERMES_DB_QUERY_TIMEOUT_SECONDS")
    if env_value:
        try:
            parsed = float(env_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    deployment_database = _deployment_database_config()
    if deployment_database is not None:
        return float(deployment_database.timeout_seconds)
    return _DEFAULT_TIMEOUT_SECONDS


def _effective_max_rows(max_rows: int | None) -> int:
    configured = _configured_max_rows()
    try:
        requested = int(max_rows or configured)
    except (TypeError, ValueError):
        requested = configured
    requested = max(1, requested)
    return min(requested, configured)


def _connect():
    url_env = _database_url_env_name()
    url = os.environ.get(url_env, "")
    if not url:
        raise RuntimeError(f"{url_env} not configured (set it in .env, e.g. sqlite:///<path>)")
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1) if ":///" in url else url.replace("sqlite://", "", 1)
        con = sqlite3.connect(path, timeout=_configured_timeout_seconds())
        con.execute("PRAGMA query_only = ON")
        con.row_factory = sqlite3.Row
        return con
    # Non-sqlite backends go through SQLAlchemy (customer installs the driver).
    try:
        from sqlalchemy import create_engine, text  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "HERMES_DB_URL is a non-sqlite backend — install SQLAlchemy + the DB "
            f"driver (e.g. psycopg2) to use db_query. ({exc})"
        ) from exc
    return _SqlaConn(create_engine(url))


class _SqlaConn:
    """Thin adapter so db_query speaks one API across sqlite3 and SQLAlchemy."""

    def __init__(self, engine):
        self._engine = engine
        self._conn = engine.connect()

    def execute(self, sql: str):
        from sqlalchemy import text

        return self._conn.execute(text(sql))

    def close(self):
        self._conn.close()
        self._engine.dispose()


def db_query(sql: str, max_rows: int = _MAX_ROWS) -> str:
    """Run a SQL query and return columns + rows as JSON.

    The DB user is SELECT-only (grant-layer enforcement); writes/truncates are
    rejected by the database itself.
    """
    _validate_read_only_sql(sql)
    effective_max_rows = _effective_max_rows(max_rows)
    deadline = time.monotonic() + _configured_timeout_seconds()
    con = _connect()
    try:
        if isinstance(con, sqlite3.Connection):
            con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        cur = con.execute(sql)
        try:
            columns = [d[0] for d in cur.description] if cur.description else []
        except Exception:
            columns = []
        fetched = cur.fetchmany(effective_max_rows + 1) if hasattr(cur, "fetchmany") else cur.fetchall()
        truncated = len(fetched) > effective_max_rows
        fetched = fetched[:effective_max_rows]
        rows = []
        for r in fetched:
            if isinstance(r, sqlite3.Row):
                rows.append({k: r[k] for k in r.keys()})
            elif hasattr(r, "_mapping"):
                rows.append(dict(r._mapping))
            else:
                rows.append(dict(zip(columns, r)))
        return json.dumps(
            {"columns": columns, "row_count": len(rows), "rows": rows, "truncated": truncated},
            ensure_ascii=False,
            default=str,
        )
    finally:
        con.close()


def _handle(args: dict, **kw) -> str:
    return db_query(sql=args["sql"], max_rows=int(args.get("max_rows") or _MAX_ROWS))


registry.register(
    name="db_query",
    toolset="db",
    schema={
        "name": "db_query",
        "description": (
            "Run a read-only SQL query against the configured business database "
            "(HERMES_DB_URL). Returns JSON {columns, row_count, rows, truncated}. "
            "Read-only is enforced at the DB grant layer; writes are rejected by the database."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query (SELECT)."},
                "max_rows": {
                    "type": "integer",
                    "description": f"Maximum rows to return (default {_MAX_ROWS}).",
                    "default": _MAX_ROWS,
                },
            },
            "required": ["sql"],
        },
    },
    handler=_handle,
)
