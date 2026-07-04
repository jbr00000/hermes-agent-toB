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
import sqlite3
from typing import Any

from tools.registry import registry

_MAX_ROWS = 200


def _connect():
    url = os.environ.get("HERMES_DB_URL", "")
    if not url:
        raise RuntimeError("HERMES_DB_URL not configured (set it in .env, e.g. sqlite:///<path>)")
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1) if ":///" in url else url.replace("sqlite://", "", 1)
        con = sqlite3.connect(path)
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
    con = _connect()
    try:
        cur = con.execute(sql)
        try:
            columns = [d[0] for d in cur.description] if cur.description else []
        except Exception:
            columns = []
        fetched = cur.fetchmany(max_rows) if hasattr(cur, "fetchmany") else cur.fetchall()
        rows = []
        for r in fetched:
            if isinstance(r, sqlite3.Row):
                rows.append({k: r[k] for k in r.keys()})
            else:
                rows.append(dict(zip(columns, r)))
        return json.dumps(
            {"columns": columns, "row_count": len(rows), "rows": rows, "truncated": len(rows) == max_rows},
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
