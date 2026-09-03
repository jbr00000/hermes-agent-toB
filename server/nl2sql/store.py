"""NL2SQL 语义层的数据访问：数据源 / 数据集 / 六类元数据 / 同步历史。

不走 StorageRepository（那里面向用户/会话/任务/知识库），本包自包含，
统一 ``session_scope`` + 返回 snake_case dict（与 repository 的返回约定一致，
由 routes 层直接序列化、前端 api.ts 做 camelCase 转换）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import func, select

from server.storage.database import session_scope
from server.storage.models import (
    Nl2sqlDataset,
    Nl2sqlDatasource,
    Nl2sqlDdl,
    Nl2sqlDimensionValue,
    Nl2sqlForeignKey,
    Nl2sqlIndex,
    Nl2sqlQaPair,
    Nl2sqlSyncHistory,
    Nl2sqlTerminology,
)
from server.storage.repository import tenant_id

# 六类元数据 kind → 模型。kind 与前端 MetaKind 一一对应。
# 注：值标 Any —— ty 看不透 SQLAlchemy 映射属性，标 type 会误报 unresolved-attribute
META_MODELS: dict[str, Any] = {
    "tables": Nl2sqlDdl,
    "terms": Nl2sqlTerminology,
    "metrics": Nl2sqlIndex,
    "dimensions": Nl2sqlDimensionValue,
    "foreignKeys": Nl2sqlForeignKey,
    "examples": Nl2sqlQaPair,
}

# 各 kind 的可写字段（routes 层校验后的白名单；id/dataset_id/时间戳由本层管）
META_FIELDS: dict[str, tuple[str, ...]] = {
    "tables": ("table_name", "ddl_content", "description", "enabled", "provider"),
    "terms": ("terminology", "terminology_explain", "synonyms", "remark", "provider"),
    "metrics": ("index_name", "index_display_name", "calculate_method", "remark", "provider"),
    "dimensions": (
        "dimension_name", "dimension_display_name", "db_data_key", "db_data_value",
        "remark", "provider",
    ),
    "foreignKeys": (
        "source_table", "source_column", "target_table", "target_column",
        "relation_desc", "provider",
    ),
    "examples": ("question", "question_sql", "remark", "provider"),
}


def _now() -> float:
    return time.time()


def _row(obj: Any) -> dict[str, Any]:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


# ------------------------------------------------------------------ 数据源


def list_datasources() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(Nl2sqlDatasource)
            .where(Nl2sqlDatasource.tenant_id == tenant_id())
            .order_by(Nl2sqlDatasource.created_at)
        ).all()
        return [_row(row) for row in rows]


def get_datasource(ds_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Nl2sqlDatasource, ds_id)
        if row is None or row.tenant_id != tenant_id():
            return None
        return _row(row)


def create_datasource(fields: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with session_scope() as session:
        row = Nl2sqlDatasource(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id(),
            created_at=now,
            updated_at=now,
            **fields,
        )
        session.add(row)
        session.flush()
        return _row(row)


def update_datasource(ds_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Nl2sqlDatasource, ds_id)
        if row is None or row.tenant_id != tenant_id():
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = _now()
        session.flush()
        return _row(row)


def delete_datasource(ds_id: str) -> bool:
    """删除数据源；其下数据集存在时由 routes 层拒绝（先删数据集）。"""
    with session_scope() as session:
        row = session.get(Nl2sqlDatasource, ds_id)
        if row is None or row.tenant_id != tenant_id():
            return False
        session.delete(row)
        return True


# ------------------------------------------------------------------ 数据集


def _dataset_counts(session, dataset_ids: list[str]) -> dict[str, dict[str, int]]:
    """批量统计每个数据集的 ddl_count（表结构条数）与 rule_count（其余五类合计）。"""
    counts = {ds_id: {"ddl_count": 0, "rule_count": 0} for ds_id in dataset_ids}
    if not dataset_ids:
        return counts
    tid = tenant_id()
    ddl_rows = session.execute(
        select(Nl2sqlDdl.dataset_id, func.count())
        .where(Nl2sqlDdl.tenant_id == tid, Nl2sqlDdl.dataset_id.in_(dataset_ids))
        .group_by(Nl2sqlDdl.dataset_id)
    ).all()
    for ds_id, n in ddl_rows:
        counts[ds_id]["ddl_count"] = int(n)
    for kind in ("terms", "metrics", "dimensions", "foreignKeys", "examples"):
        model = META_MODELS[kind]
        rows = session.execute(
            select(model.dataset_id, func.count())
            .where(model.tenant_id == tid, model.dataset_id.in_(dataset_ids))
            .group_by(model.dataset_id)
        ).all()
        for ds_id, n in rows:
            counts[ds_id]["rule_count"] += int(n)
    return counts


def list_datasets() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(Nl2sqlDataset)
            .where(Nl2sqlDataset.tenant_id == tenant_id())
            .order_by(Nl2sqlDataset.created_at)
        ).all()
        datasets = [_row(row) for row in rows]
        counts = _dataset_counts(session, [item["id"] for item in datasets])
    for item in datasets:
        item.update(counts[item["id"]])
    return datasets


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Nl2sqlDataset, dataset_id)
        if row is None or row.tenant_id != tenant_id():
            return None
        item = _row(row)
        item.update(_dataset_counts(session, [dataset_id])[dataset_id])
        return item


def create_dataset(fields: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with session_scope() as session:
        row = Nl2sqlDataset(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id(),
            created_at=now,
            updated_at=now,
            **fields,
        )
        session.add(row)
        session.flush()
        item = _row(row)
    item.update({"ddl_count": 0, "rule_count": 0})
    return item


def update_dataset(dataset_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Nl2sqlDataset, dataset_id)
        if row is None or row.tenant_id != tenant_id():
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = _now()
        session.flush()
        item = _row(row)
        item.update(_dataset_counts(session, [dataset_id])[dataset_id])
        return item


def count_datasets_by_datasource(ds_id: str) -> int:
    with session_scope() as session:
        return int(session.scalar(
            select(func.count())
            .select_from(Nl2sqlDataset)
            .where(Nl2sqlDataset.tenant_id == tenant_id(), Nl2sqlDataset.datasource_id == ds_id)
        ) or 0)


def delete_dataset(dataset_id: str) -> bool:
    """删除数据集并级联清空其六类元数据与同步历史。"""
    with session_scope() as session:
        row = session.get(Nl2sqlDataset, dataset_id)
        if row is None or row.tenant_id != tenant_id():
            return False
        session.delete(row)
        for model in (*META_MODELS.values(), Nl2sqlSyncHistory):
            session.query(model).filter(
                model.tenant_id == tenant_id(), model.dataset_id == dataset_id
            ).delete()
        return True


# ------------------------------------------------------------------ 六类元数据


def get_meta_bundle(dataset_id: str) -> dict[str, list[dict[str, Any]]]:
    """一个数据集的全量元数据（六类打包，对应前端 DatasetMetaBundle）。"""
    bundle: dict[str, list[dict[str, Any]]] = {}
    with session_scope() as session:
        for kind, model in META_MODELS.items():
            rows = session.scalars(
                select(model)
                .where(model.tenant_id == tenant_id(), model.dataset_id == dataset_id)
                .order_by(model.created_at)
            ).all()
            bundle[kind] = [_row(row) for row in rows]
    return bundle


def upsert_meta_item(
    dataset_id: str, kind: str, item_id: str | None, fields: dict[str, Any]
) -> dict[str, Any]:
    """新增（item_id=None）或更新一条元数据。唯一键冲突抛 IntegrityError 由 routes 转 409。"""
    model = META_MODELS[kind]
    allowed = META_FIELDS[kind]
    now = _now()
    with session_scope() as session:
        if item_id is not None:
            row = session.get(model, item_id)
            if row is None or row.tenant_id != tenant_id() or row.dataset_id != dataset_id:
                raise KeyError(item_id)
            for key, value in fields.items():
                if key in allowed:
                    setattr(row, key, value)
            row.updated_at = now
        else:
            row = model(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id(),
                dataset_id=dataset_id,
                created_at=now,
                updated_at=now,
                **{key: value for key, value in fields.items() if key in allowed},
            )
            session.add(row)
        session.flush()
        return _row(row)


def delete_meta_item(dataset_id: str, kind: str, item_id: str) -> bool:
    model = META_MODELS[kind]
    with session_scope() as session:
        row = session.get(model, item_id)
        if row is None or row.tenant_id != tenant_id() or row.dataset_id != dataset_id:
            return False
        session.delete(row)
        return True


def clear_dataset_meta(dataset_id: str) -> None:
    with session_scope() as session:
        for model in META_MODELS.values():
            session.query(model).filter(
                model.tenant_id == tenant_id(), model.dataset_id == dataset_id
            ).delete()


# ------------------------------------------------------------------ 同步历史


def list_sync_history(dataset_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(Nl2sqlSyncHistory)
            .where(
                Nl2sqlSyncHistory.tenant_id == tenant_id(),
                Nl2sqlSyncHistory.dataset_id == dataset_id,
            )
            .order_by(Nl2sqlSyncHistory.created_at.desc())
            .limit(limit)
        ).all()
        return [_row(row) for row in rows]
