from __future__ import annotations

import json
import sqlite3

import pytest

from tools.db_query import db_query


def _make_db(tmp_path):
    path = tmp_path / "customer.db"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        con.executemany(
            "INSERT INTO customers (name) VALUES (?)",
            [(f"customer-{idx}",) for idx in range(1, 6)],
        )
        con.commit()
    finally:
        con.close()
    return path


def test_db_query_rejects_non_readonly_sql(monkeypatch, tmp_path) -> None:
    db_path = _make_db(tmp_path)
    monkeypatch.setenv("HERMES_DB_URL", f"sqlite:///{db_path}")

    with pytest.raises(ValueError, match="read-only"):
        db_query("DELETE FROM customers")

    payload = json.loads(db_query("SELECT count(*) AS total FROM customers"))
    assert payload["rows"] == [{"total": 5}]


def test_db_query_applies_row_cap_and_truncates(monkeypatch, tmp_path) -> None:
    db_path = _make_db(tmp_path)
    monkeypatch.setenv("HERMES_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HERMES_DB_QUERY_MAX_ROWS", "2")

    payload = json.loads(db_query("SELECT id, name FROM customers ORDER BY id", max_rows=999))

    assert payload["row_count"] == 2
    assert payload["truncated"] is True
    assert payload["rows"] == [
        {"id": 1, "name": "customer-1"},
        {"id": 2, "name": "customer-2"},
    ]


def test_db_query_can_read_url_env_from_deployment_config(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    db_path = _make_db(tmp_path)
    (home / "deployment.yaml").write_text(
        """
database:
  url_env: CUSTOMER_ANALYTICS_DB_URL
  max_rows: 10
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DB_URL", raising=False)
    monkeypatch.setenv("CUSTOMER_ANALYTICS_DB_URL", f"sqlite:///{db_path}")

    payload = json.loads(db_query("SELECT count(*) AS total FROM customers"))

    assert payload["rows"] == [{"total": 5}]
