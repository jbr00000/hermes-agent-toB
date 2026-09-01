"""Import the BULL NL2SQL benchmark databases (sqlite) into MySQL for dev testing.

Reads the three CCKS2022 databases from ``nl2sql_data/BULL/BULL/database_cn/``
and rebuilds them as MySQL schemas ``nl2sql_fund`` / ``nl2sql_stock`` /
``nl2sql_macro`` in the dev MySQL container (127.0.0.1:13306).

Root credentials come from ``.env.compose`` (HERMES_MYSQL_ROOT_PASSWORD /
HERMES_MYSQL_PORT) — never hardcode secrets here.

Optionally creates a SELECT-only account ``hermes_nl2sql_ro`` (GRANT-layer
read-only, per the to-B architecture decision) when --ro-password is given.

Usage:
    python scripts/import_nl2sql_data.py [--dbs fund,stock,macro] \
        [--ro-password <pw>] [--verify-samples 10]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine, URL

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "nl2sql_data" / "BULL" / "BULL" / "database_cn"
DEV_JSON = REPO_ROOT / "nl2sql_data" / "BULL" / "BULL" / "BULL-cn" / "dev.json"

# dataset key -> (sqlite filename, target MySQL schema)
DATASETS = {
    "fund": ("ccks_fund", "nl2sql_fund"),
    "stock": ("ccks_stock", "nl2sql_stock"),
    "macro": ("ccks_macro", "nl2sql_macro"),
}

_INSERT_CHUNK = 500
# utf8mb4 indexed/varchar upper bound safety cap
_MAX_VARCHAR_LEN = 1024


def _load_compose_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO_ROOT / ".env.compose"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _mysql_root_engine(compose_env: dict[str, str]) -> Engine:
    password = compose_env["HERMES_MYSQL_ROOT_PASSWORD"]
    port = compose_env.get("HERMES_MYSQL_PORT", "13306")
    return create_engine(
        f"mysql+pymysql://root:{password}@127.0.0.1:{port}/?charset=utf8mb4"
    )


def _generic_column(col) -> Column:
    """Map a reflected sqlite column to a MySQL-safe generic column."""
    t = col.type
    try:
        t = t.as_generic()
    except Exception:  # sqlite-only affinity types
        t = Text()
    if isinstance(t, String) and not isinstance(t, Text):
        if t.length is None:
            t = Text()
        elif t.length > _MAX_VARCHAR_LEN:
            t = String(length=_MAX_VARCHAR_LEN)
    elif not isinstance(
        t, (Integer, BigInteger, Float, Numeric, Text, String, Date, DateTime, LargeBinary)
    ):
        t = Text()  # permissive fallback for odd sqlite affinities
    return Column(
        col.name,
        t,
        primary_key=bool(col.primary_key),
        autoincrement=False,
        nullable=True,  # dev import: never reject rows on NULL constraints
    )


def import_dataset(engine: Engine, sqlite_path: Path, schema: str) -> dict[str, tuple[int, int]]:
    """Rebuild one sqlite database as a MySQL schema. Returns {table: (src, dst)} row counts."""
    src = create_engine(f"sqlite:///{sqlite_path}")
    src_meta = MetaData()
    src_meta.reflect(bind=src)

    with engine.begin() as con:
        con.execute(text(f"DROP DATABASE IF EXISTS `{schema}`"))
        con.execute(
            text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )

    dst_url = URL.create(
        "mysql+pymysql",
        username=engine.url.username,
        password=engine.url.password,
        host=engine.url.host,
        port=engine.url.port,
        database=schema,
        query={"charset": "utf8mb4"},
    )
    dst = create_engine(dst_url)
    dst_meta = MetaData()
    counts: dict[str, tuple[int, int]] = {}

    for src_table in src_meta.tables.values():
        dst_table = Table(
            src_table.name, dst_meta, *[_generic_column(c) for c in src_table.columns]
        )
        dst_table.create(bind=dst)
        with src.connect() as scon, dst.begin() as dcon:
            result = scon.execute(src_table.select())
            inserted = 0
            while True:
                rows = result.fetchmany(_INSERT_CHUNK)
                if not rows:
                    break
                dcon.execute(dst_table.insert(), [dict(r._mapping) for r in rows])
                inserted += len(rows)
        with src.connect() as scon:
            src_count = scon.execute(
                text(f'SELECT COUNT(*) FROM "{src_table.name}"')
            ).scalar_one()
        counts[src_table.name] = (src_count, inserted)
        flag = "OK " if src_count == inserted else "MISMATCH"
        print(f"    [{flag}] {src_table.name}: {inserted}/{src_count} rows", flush=True)
    return counts


def create_readonly_user(engine: Engine, password: str, schemas: list[str]) -> None:
    """SELECT-only account — read-only enforced at the GRANT layer, not just in the tool."""
    if any(ch in password for ch in "'\"\\"):
        raise ValueError("ro password must not contain quotes or backslashes")
    with engine.begin() as con:
        con.execute(text(f"CREATE USER IF NOT EXISTS 'hermes_nl2sql_ro'@'%' IDENTIFIED BY '{password}'"))
        con.execute(text(f"ALTER USER 'hermes_nl2sql_ro'@'%' IDENTIFIED BY '{password}'"))
        for schema in schemas:
            con.execute(text(f"GRANT SELECT ON `{schema}`.* TO 'hermes_nl2sql_ro'@'%'"))
        con.execute(text("FLUSH PRIVILEGES"))
    print(f"  read-only user hermes_nl2sql_ro granted SELECT on: {', '.join(schemas)}")


def verify_samples(engine: Engine, n: int) -> None:
    """Run n gold SQLs from dev.json against the imported MySQL schemas."""
    import json

    samples: dict[str, list[dict]] = {}
    for item in json.loads(DEV_JSON.read_text(encoding="utf-8")):
        samples.setdefault(item["db_name"], []).append(item)

    for ds_key, (sqlite_name, schema) in DATASETS.items():
        entries = samples.get(sqlite_name, [])[:n]
        if not entries:
            continue
        ok = 0
        with engine.connect() as con:
            for entry in entries:
                try:
                    con.execute(text(f"USE `{schema}`"))
                    con.execute(text(entry["sql_query"]))
                    ok += 1
                except Exception as exc:
                    print(f"    [FAIL] q_id={entry['q_id']} {entry['question'][:40]}… :: {type(exc).__name__}: {str(exc)[:120]}")
        print(f"  {schema}: {ok}/{len(entries)} gold SQL executed OK", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbs", default="fund,stock,macro")
    parser.add_argument("--ro-password", default=os.environ.get("NL2SQL_RO_PASSWORD", ""))
    parser.add_argument("--verify-samples", type=int, default=10)
    args = parser.parse_args()

    compose_env = _load_compose_env()
    engine = _mysql_root_engine(compose_env)

    selected = [k.strip() for k in args.dbs.split(",") if k.strip()]
    schemas: list[str] = []
    for key in selected:
        sqlite_name, schema = DATASETS[key]
        sqlite_path = DATASET_DIR / sqlite_name / f"{sqlite_name}.sqlite"
        if not sqlite_path.is_file():
            print(f"  [SKIP] {sqlite_path} not found")
            continue
        print(f" importing {sqlite_name} -> {schema} …", flush=True)
        import_dataset(engine, sqlite_path, schema)
        schemas.append(schema)

    if args.ro_password:
        create_readonly_user(engine, args.ro_password, schemas)
    else:
        print("  (no --ro-password given; skipping hermes_nl2sql_ro creation)")

    if args.verify_samples:
        print(" verifying gold SQL samples …", flush=True)
        verify_samples(engine, args.verify_samples)

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
