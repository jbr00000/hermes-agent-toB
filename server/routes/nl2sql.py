"""NL2SQL（问数）语义层路由：数据源连接 / 数据集 / 六类元数据 / Excel / 同步历史。

移植自 lone-ai Java 后端的 nl2sql 包能力（FastAPI + SQLAlchemy 重写）：
  - 数据源：CRUD + 测试连接（密码 Fernet 加密落库，API 永不回传明文）
  - 数据集：CRUD（删除级联清空元数据），ddl_count/rule_count 实时统计
  - 元数据：六类（表结构/术语/指标/维度/范例/外键）打包读取 + 按 kind upsert/删除/清空
  - Excel：模板下载 / 导出 / 导入 preview→confirm 两步
  - sync-ddl：从数据源抓 DDL 直接灌入表结构元数据
  - 三端同步历史：只读列表（同步执行器在阶段5 实现）

读取 = 登录用户（问数页要选数据集）；变更 = admin-only（对齐 knowledge 路由口径）。
问数主链路 ``POST /nl2sql/ask`` 在阶段3 加入本文件。
"""
from __future__ import annotations

import logging
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from server.deps import get_current_user, require_admin
from server.nl2sql import excel, store
from server.nl2sql.datasource import DatasourceError, fetch_table_ddls, test_connection
from server.storage import get_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])

_read_router = APIRouter(dependencies=[Depends(get_current_user)])
_admin_router = APIRouter(dependencies=[Depends(require_admin)])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ 入参模型


class DatasourcePayload(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    db_type: str = Field(pattern="^(mysql|postgresql)$")
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=128)
    schema_name: str | None = Field(default=None, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    # 编辑时留空 = 不修改密码（前端 write-only 约定）
    password: str | None = Field(default=None, max_length=256)
    description: str | None = None


class DatasetPayload(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    datasource_id: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    enabled: bool = True


class MetaItemPayload(BaseModel):
    """六类元数据共用的 upsert 载荷：id 为空 = 新增；字段白名单见 store.META_FIELDS。"""

    id: str | None = None
    fields: dict[str, object] = {}


class ImportConfirmPayload(BaseModel):
    preview_id: str = Field(min_length=1)


# ------------------------------------------------------------------ 序列化


def _datasource_out(row: dict) -> dict:
    """脱敏：密码密文不下发，只回 has_password 供表单显示「已设置」。"""
    out = {key: value for key, value in row.items() if key != "password_enc"}
    out["has_password"] = bool(row.get("password_enc"))
    return out


def _datasource_or_404(ds_id: str) -> dict:
    row = store.get_datasource(ds_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return row


def _dataset_or_404(dataset_id: str) -> dict:
    row = store.get_dataset(dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return row


def _check_kind(kind: str) -> str:
    if kind not in store.META_MODELS:
        raise HTTPException(status_code=404, detail=f"未知元数据类型: {kind}")
    return kind


def _xlsx_response(content: bytes, filename: str) -> Response:
    quoted = urllib.parse.quote(filename)
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


def _datasource_fields(payload: DatasourcePayload, *, creating: bool) -> dict:
    """payload → 存储字段；密码单独加密，留空时编辑不改、新建为 NULL。"""
    fields: dict = {
        "name": payload.name.strip(),
        "db_type": payload.db_type,
        "host": payload.host.strip(),
        "port": payload.port,
        "database_name": payload.database_name.strip(),
        "schema_name": (payload.schema_name or "").strip() or None,
        "username": payload.username.strip(),
        "description": payload.description,
    }
    if payload.password:
        from server.nl2sql.crypto import encrypt_password

        fields["password_enc"] = encrypt_password(payload.password)
    elif creating:
        fields["password_enc"] = None
    return fields


# ------------------------------------------------------------------ 数据源


@_read_router.get("/datasources")
def list_datasources():
    return {"datasources": [_datasource_out(row) for row in store.list_datasources()]}


@_admin_router.post("/datasources", status_code=201)
def create_datasource(payload: DatasourcePayload):
    try:
        return {"datasource": _datasource_out(store.create_datasource(_datasource_fields(payload, creating=True)))}
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"数据源名称「{payload.name}」已存在")


@_admin_router.put("/datasources/{ds_id}")
def update_datasource(ds_id: str, payload: DatasourcePayload):
    fields = _datasource_fields(payload, creating=False)
    # 连接参数变化 → 旧的测试结果失效
    fields.update({"last_test_status": None, "last_test_message": None, "last_tested_at": None})
    try:
        row = store.update_datasource(ds_id, fields)
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"数据源名称「{payload.name}」已存在")
    if row is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {"datasource": _datasource_out(row)}


@_admin_router.delete("/datasources/{ds_id}")
def delete_datasource(ds_id: str, user: dict = Depends(require_admin)):
    _datasource_or_404(ds_id)
    datasets = store.count_datasets_by_datasource(ds_id)
    if datasets > 0:
        raise HTTPException(status_code=409, detail=f"该数据源下还有 {datasets} 个数据集，请先删除数据集")
    store.delete_datasource(ds_id)
    get_repository().record_audit_event(
        event_type="nl2sql_datasource_delete", conversation_id=None, user_id=user["id"],
        status="completed", mode=None, metadata={"datasource_id": ds_id}, error=None,
    )
    return {"ok": True}


@_admin_router.post("/datasources/{ds_id}/test")
def test_datasource(ds_id: str):
    row = _datasource_or_404(ds_id)
    result = test_connection(row)
    updated = store.update_datasource(ds_id, {
        "last_test_status": "connected" if result["success"] else "failed",
        "last_test_message": result["message"],
        "last_tested_at": time.time(),
    })
    if updated is None:  # 上面刚取到，理论不可达；防御并发删除
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {**result, "datasource": _datasource_out(updated)}


# ------------------------------------------------------------------ 数据集


@_read_router.get("/datasets")
def list_datasets():
    return {"datasets": store.list_datasets()}


@_admin_router.post("/datasets", status_code=201)
def create_dataset(payload: DatasetPayload):
    _datasource_or_404(payload.datasource_id)
    fields = {
        "name": payload.name.strip(),
        "datasource_id": payload.datasource_id,
        "description": payload.description,
        "system_prompt": payload.system_prompt,
        "enabled": payload.enabled,
    }
    try:
        return {"dataset": store.create_dataset(fields)}
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"数据集名称「{payload.name}」已存在")


@_admin_router.put("/datasets/{dataset_id}")
def update_dataset(dataset_id: str, payload: DatasetPayload):
    _dataset_or_404(dataset_id)
    _datasource_or_404(payload.datasource_id)
    fields = {
        "name": payload.name.strip(),
        "datasource_id": payload.datasource_id,
        "description": payload.description,
        "system_prompt": payload.system_prompt,
        "enabled": payload.enabled,
    }
    try:
        row = store.update_dataset(dataset_id, fields)
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"数据集名称「{payload.name}」已存在")
    if row is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return {"dataset": row}


@_admin_router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, user: dict = Depends(require_admin)):
    _dataset_or_404(dataset_id)
    store.delete_dataset(dataset_id)
    get_repository().record_audit_event(
        event_type="nl2sql_dataset_delete", conversation_id=None, user_id=user["id"],
        status="completed", mode=None, metadata={"dataset_id": dataset_id}, error=None,
    )
    return {"ok": True}


# ------------------------------------------------------------------ 六类元数据


@_read_router.get("/datasets/{dataset_id}/meta")
def get_meta(dataset_id: str):
    _dataset_or_404(dataset_id)
    return {"meta": store.get_meta_bundle(dataset_id)}


@_admin_router.put("/datasets/{dataset_id}/meta/{kind}")
def upsert_meta(dataset_id: str, kind: str, payload: MetaItemPayload):
    _dataset_or_404(dataset_id)
    _check_kind(kind)
    try:
        row = store.upsert_meta_item(dataset_id, kind, payload.id, payload.fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="元数据记录不存在")
    except IntegrityError:
        raise HTTPException(status_code=409, detail="已存在相同关键字段的记录")
    return {"item": row}


@_admin_router.delete("/datasets/{dataset_id}/meta/{kind}/{item_id}")
def delete_meta(dataset_id: str, kind: str, item_id: str):
    _dataset_or_404(dataset_id)
    _check_kind(kind)
    if not store.delete_meta_item(dataset_id, kind, item_id):
        raise HTTPException(status_code=404, detail="元数据记录不存在")
    return {"ok": True}


@_admin_router.post("/datasets/{dataset_id}/meta/clear")
def clear_meta(dataset_id: str, user: dict = Depends(require_admin)):
    dataset = _dataset_or_404(dataset_id)
    store.clear_dataset_meta(dataset_id)
    get_repository().record_audit_event(
        event_type="nl2sql_meta_clear", conversation_id=None, user_id=user["id"],
        status="completed", mode=None,
        metadata={"dataset_id": dataset_id, "dataset_name": dataset["name"]}, error=None,
    )
    return {"ok": True}


@_admin_router.post("/datasets/{dataset_id}/meta/sync-ddl")
def sync_ddl(dataset_id: str):
    """从数据集对应的数据源抓取全部表 DDL，灌入表结构元数据。

    已存在的表（按 table_name 匹配）只更新 ddl_content/description，
    不触碰 enabled；新表默认启用。
    """
    dataset = _dataset_or_404(dataset_id)
    datasource = _datasource_or_404(dataset["datasource_id"])
    try:
        fetched = fetch_table_ddls(datasource)
    except DatasourceError as exc:
        raise HTTPException(status_code=502, detail=f"从数据源抓取失败: {exc}")

    existing = store.get_meta_bundle(dataset_id)["tables"]
    by_name = {row["table_name"]: row for row in existing}
    created = updated = 0
    for item in fetched:
        hit = by_name.get(item["table_name"])
        if hit is None:
            store.upsert_meta_item(dataset_id, "tables", None, item)
            created += 1
        elif (
            hit["ddl_content"] != item["ddl_content"]
            or (hit.get("description") or "") != item["description"]
        ):
            store.upsert_meta_item(dataset_id, "tables", hit["id"], item)
            updated += 1
    return {"created": created, "updated": updated, "total": len(fetched)}


# ------------------------------------------------------------------ Excel 模板 / 导出 / 导入


@_read_router.get("/datasets/{dataset_id}/meta/template")
def download_template(dataset_id: str):
    dataset = _dataset_or_404(dataset_id)
    return _xlsx_response(
        excel.build_template(dataset["name"]),
        f"元数据配置模板-{dataset['name']}.xlsx",
    )


@_read_router.get("/datasets/{dataset_id}/meta/export")
def export_meta(dataset_id: str):
    dataset = _dataset_or_404(dataset_id)
    bundle = store.get_meta_bundle(dataset_id)
    return _xlsx_response(
        excel.build_export(dataset["name"], bundle),
        f"元数据配置导出数据-{dataset['name']}.xlsx",
    )


@_admin_router.post("/datasets/{dataset_id}/meta/import/preview")
def import_preview(dataset_id: str, file: UploadFile):
    _dataset_or_404(dataset_id)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    content = file.file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 20MB 上限")
    try:
        return excel.create_preview(dataset_id, content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Excel 解析失败: {str(exc)[:200]}")


@_admin_router.post("/datasets/{dataset_id}/meta/import/confirm")
def import_confirm(dataset_id: str, payload: ImportConfirmPayload, user: dict = Depends(require_admin)):
    _dataset_or_404(dataset_id)
    try:
        result = excel.confirm_preview(dataset_id, payload.preview_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="预览已过期或不存在，请重新上传")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    get_repository().record_audit_event(
        event_type="nl2sql_meta_import", conversation_id=None, user_id=user["id"],
        status="completed", mode=None,
        metadata={"dataset_id": dataset_id, **result}, error=None,
    )
    return result


# ------------------------------------------------------------------ 三端同步历史（执行器阶段5 落地）


@_read_router.get("/datasets/{dataset_id}/sync-history")
def sync_history(dataset_id: str):
    _dataset_or_404(dataset_id)
    return {"history": store.list_sync_history(dataset_id)}


router.include_router(_read_router)
router.include_router(_admin_router)
