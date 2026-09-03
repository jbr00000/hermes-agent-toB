"""把 build_nl2sql_metadata_xlsx.py 生成的冷启动 Excel 走图4 真实导入链路灌库。

完整走 HTTP API（与前端图4 同一组端点），不直接写库：
  登录 admin → 按名 find-or-create 数据源（测试连接）→ find-or-create 数据集
  → POST meta/import/preview → POST meta/import/confirm → 打印每库落库摘要

密钥读取纪律：admin 密码与数据源密码从 .hermes-dev/.env 读入内存，永不打印。
幂等：重复执行时数据源/数据集按名复用，导入按关键字段去重（create=0）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

_DATASETS = [
    # (xlsx 文件名片段, 数据源名, 数据库名, 数据集名, 数据集说明)
    ("nl2sql_fund", "公募基金问数库", "nl2sql_fund", "公募基金问数", "BULL-cn 公募基金领域演示数据集"),
    ("nl2sql_stock", "股票问数库", "nl2sql_stock", "股票问数", "BULL-cn 股票领域演示数据集"),
    ("nl2sql_macro", "宏观经济问数库", "nl2sql_macro", "宏观经济问数", "BULL-cn 宏观经济领域演示数据集"),
]


def _read_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _check(response: requests.Response, what: str) -> dict:
    if response.status_code >= 400:
        print(f"ERROR: {what} → {response.status_code} {response.text[:300]}", file=sys.stderr)
        sys.exit(1)
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="走真实 API 把冷启动 Excel 灌入 NL2SQL 元数据")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=".hermes-dev/.env")
    parser.add_argument("--xlsx-dir", default="nl2sql_data/metadata_xlsx")
    args = parser.parse_args()

    env = _read_env(Path(args.env_file))
    admin_password = env.get("HERMES_ADMIN_PASSWORD")
    ds_password = env.get("NL2SQL_DEMO_DS_PASSWORD")
    if not admin_password or not ds_password:
        print("ERROR: .env 缺 HERMES_ADMIN_PASSWORD 或 NL2SQL_DEMO_DS_PASSWORD", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    session = requests.Session()

    token = _check(
        session.post(f"{base}/auth/login", json={"username": "admin", "password": admin_password}),
        "admin 登录",
    )["access_token"]
    session.headers["Authorization"] = f"Bearer {token}"

    datasources = {
        row["name"]: row
        for row in _check(session.get(f"{base}/nl2sql/datasources"), "列数据源")["datasources"]
    }
    datasets = {
        row["name"]: row
        for row in _check(session.get(f"{base}/nl2sql/datasets"), "列数据集")["datasets"]
    }

    for file_key, ds_name, db_name, dataset_name, description in _DATASETS:
        if ds_name in datasources:
            datasource = datasources[ds_name]
        else:
            datasource = _check(
                session.post(f"{base}/nl2sql/datasources", json={
                    "name": ds_name, "db_type": "mysql", "host": "127.0.0.1", "port": 13306,
                    "database_name": db_name, "username": "hermes_nl2sql_ro",
                    "password": ds_password,
                }),
                f"建数据源 {ds_name}",
            )["datasource"]
        test = _check(
            session.post(f"{base}/nl2sql/datasources/{datasource['id']}/test"), f"测试连接 {ds_name}"
        )
        if not test["success"]:
            print(f"ERROR: {ds_name} 连接失败: {test['message']}", file=sys.stderr)
            return 1

        if dataset_name in datasets:
            dataset = datasets[dataset_name]
        else:
            dataset = _check(
                session.post(f"{base}/nl2sql/datasets", json={
                    "name": dataset_name, "datasource_id": datasource["id"],
                    "description": description,
                }),
                f"建数据集 {dataset_name}",
            )["dataset"]

        xlsx = next(Path(args.xlsx_dir).glob(f"*{file_key}*.xlsx"))
        preview = _check(
            session.post(
                f"{base}/nl2sql/datasets/{dataset['id']}/meta/import/preview",
                files={"file": (xlsx.name, xlsx.read_bytes(),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            ),
            f"导入预览 {dataset_name}",
        )
        confirmed = _check(
            session.post(
                f"{base}/nl2sql/datasets/{dataset['id']}/meta/import/confirm",
                json={"preview_id": preview["preview_id"]},
            ),
            f"导入确认 {dataset_name}",
        )
        summaries = {
            kind: s for kind, s in preview["type_summaries"].items()
            if s["read"] or s["error"]
        }
        print(
            f"[{dataset_name}] 连接 {test['latency_ms']}ms；导入 {xlsx.name}"
            f" → created={confirmed['created']} updated={confirmed['updated']}"
        )
        for kind, s in summaries.items():
            print(
                f"    {kind}: 读 {s['read']} / 新增 {s['create']} / 更新 {s['update']}"
                f" / 重复 {s['duplicate']} / 错误 {s['error']}"
            )
        if preview["errors"]:
            print(f"    行级错误示例: {preview['errors'][:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
