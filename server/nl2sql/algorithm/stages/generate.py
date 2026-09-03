"""generate 阶段 = lone-ai phase6-8：三路召回 → 选表/表分析 → SQL生成+安检+执行。

phase5（问题分解）已删除：单数据集 steps=None；跨数据集把完整问题作为单一步骤。
SQL 生成/安检/执行共享 4 次尝试（``_MAX_ATTEMPTS``），错误累积进下一轮上下文。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from .. import Nl2sqlError
from ..services import SQLExecutionService

if TYPE_CHECKING:
    from ..orchestrator import Nl2sqlOrchestrator

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 4  # lone-ai：SQL 生成+执行共享 4 次尝试


# ------------------------------------------------------------------ 召回 + 选表（phase6）


async def prepare_single(
    orch: "Nl2sqlOrchestrator",
    phase_result: dict[str, Any],
    question: str,
    dataset: dict[str, Any],
    dataset_id: str,
    not_select: dict[str, Any],
) -> list[str]:
    """单数据集 phase6：三路召回 + 全量选表 + 表分析。返回选中的表名列表。"""
    retrieval_result = await orch.retrieval.retrieve_all(question, dataset_id)
    phase_result["few_shots"] = retrieval_result["qa_pair"]
    phase_result["index"] = retrieval_result["index"]
    phase_result["terminology"] = retrieval_result["terminology"]
    phase_result["dimension"] = dict(not_select)

    selected_tables, table_ddls = await orch.schema_selection.select_schema(dataset_id)
    phase_result["selected_tables"] = selected_tables
    phase_result["table_ddls"] = table_ddls
    if not table_ddls:
        raise Nl2sqlError(f"数据集「{dataset['name']}」没有已启用的表结构，请先在元数据配置中启用")

    _is_multitable, join_path = orch.table_analysis.analyze_tables(selected_tables, dataset_id)
    phase_result["join_path"] = join_path
    return selected_tables


async def prepare_cross(
    orch: "Nl2sqlOrchestrator",
    phase_result: dict[str, Any],
    question: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """跨数据集 phase6：每数据集召回 + LLM 筛表 + 表分析，合并去重。返回合并后的表名列表。"""
    combined_few_shots: list[dict] = []
    combined_index: list[dict] = []
    combined_terminology: list[dict] = []
    combined_dimension: dict[str, Any] = {}
    combined_tables: list[str] = []
    combined_ddls: list[str] = []
    join_paths: list[str] = []

    for context in contexts:
        ds_id = context["dataset_id"]
        retrieval_result = await orch.retrieval.retrieve_all(question, ds_id)
        context["terminology"] = retrieval_result["terminology"]

        selected_tables, table_ddls = await orch.schema_selection.select_schema_for_cross_dataset(
            question,
            context.get("dimension", {}),
            context["terminology"],
            ds_id,
            required_tables=[],  # lone-ai 的水项目必选表已删除
        )
        context["selected_tables"] = selected_tables
        context["table_ddls"] = table_ddls
        _is_multitable, join_path = orch.table_analysis.analyze_tables(selected_tables, ds_id)
        context["join_path"] = join_path

        _merge_retrieval_items(combined_few_shots, retrieval_result["qa_pair"])
        _merge_retrieval_items(combined_index, retrieval_result["index"])
        _merge_retrieval_items(combined_terminology, retrieval_result["terminology"])
        _merge_dimension_items(combined_dimension, context.get("dimension", {}))
        for table_name in selected_tables:
            if table_name not in combined_tables:
                combined_tables.append(table_name)
        for ddl_content in table_ddls:
            if ddl_content not in combined_ddls:
                combined_ddls.append(ddl_content)
        if join_path:
            join_paths.append(f"【dataset_id={ds_id}内部关联】\n{join_path}")

    if not combined_ddls:
        raise Nl2sqlError("所选数据集没有已启用的表结构，请先在元数据配置中启用")

    phase_result["few_shots"] = combined_few_shots
    phase_result["index"] = combined_index
    phase_result["terminology"] = combined_terminology
    phase_result["dimension"] = combined_dimension
    phase_result["selected_tables"] = combined_tables
    phase_result["table_ddls"] = combined_ddls
    phase_result["join_path"] = "\n\n".join(join_paths)
    return combined_tables


# ------------------------------------------------------------------ 生成/安检/执行重试环（phase7+8）


async def generate_and_execute(
    orch: "Nl2sqlOrchestrator",
    phase_result: dict[str, Any],
    execution: SQLExecutionService,
    db_type: str,
) -> dict[str, Any]:
    """lone-ai phase7+8：SQL 生成/安检/执行共享 4 次尝试，错误累积进上下文。"""
    attempt = 0
    exist_error_information: list[dict[str, str]] = []
    while attempt < _MAX_ATTEMPTS:
        phase_result = await orch.sql_generation.generate_sql(
            phase_result,
            question=phase_result["clarified_question"],
            table_ddl=phase_result["table_ddls"],
            join_path=phase_result["join_path"],
            steps=phase_result.get("query_steps"),
            few_shots_information=phase_result["few_shots"],
            index_information=phase_result["index"],
            dimension_information=phase_result["dimension"],
            terminology_information=phase_result["terminology"],
            db_type=db_type,
            exist_error_information=exist_error_information,
            temperature=0.1,
        )
        orch._token_total += phase_result.get("sql_gen_tokens", 0)

        if phase_result.get("sql_gen_status") == "error":
            attempt += 1
            exist_error_information.extend(phase_result.get("sql_gen_error_information", []))
            logger.info("问数第 %s 次尝试 SQL 生成失败", attempt)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(1)
            continue

        _replace_tail_limit_one(phase_result)
        is_passed, blocked_reason, _warnings = orch.security_check.check_security(
            phase_result["sql_content"]
        )
        if not is_passed:
            phase_result["sql_check_status"] = "error"
            phase_result["sql_check_error_information"] = [
                {"role": "user", "content": f"SQL安全检查失败：{blocked_reason}，请重新调整输出SQL语句"}
            ]
            attempt += 1
            exist_error_information.extend(phase_result["sql_check_error_information"])
            logger.info("问数第 %s 次尝试 SQL 安检失败: %s", attempt, blocked_reason)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(1)
            continue
        phase_result["sql_check_status"] = "success"
        phase_result["sql_check_error_information"] = []

        # SQL 生成+安检通过 → 先推给前端展示，再执行
        orch._emit("sql", {
            "sql": phase_result["sql_content"],
            "explanation": phase_result.get("explain_content", ""),
        })

        phase_result = execution.execute_sql(phase_result)
        if phase_result.get("sql_exec_status") == "error":
            attempt += 1
            exist_error_information.extend(phase_result.get("sql_exec_error_information", []))
            logger.info("问数第 %s 次尝试 SQL 执行失败", attempt)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(1)
            continue

        phase_result["attempts"] = attempt + 1
        break
    else:
        phase_result["attempts"] = _MAX_ATTEMPTS
    return phase_result


# ------------------------------------------------------------------ 小工具


def _replace_tail_limit_one(phase_result: dict[str, Any]) -> None:
    """SQL 执行前仅处理末尾 limit 1（避免误改子查询/CTE 里的 limit 1）。"""
    sql_content = str(phase_result.get("sql_content") or "")
    tail_text = sql_content[-10:]
    if not re.search(r"\blimit 1\b", tail_text, flags=re.IGNORECASE):
        return
    new_tail = re.sub(r"\blimit 1\b", "limit 10", tail_text, count=1, flags=re.IGNORECASE)
    phase_result["sql_content"] = f"{sql_content[:-10]}{new_tail}"
    logger.info("[SQL预处理] 检测到 SQL 末尾 limit 1，已替换为 limit 10")


def _merge_retrieval_items(target: list[dict[str, Any]], source: list[dict[str, Any]]) -> None:
    """按内容去重合并召回信息。"""
    exists = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in target}
    for item in source or []:
        fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint not in exists:
            target.append(item)
            exists.add(fingerprint)


def _merge_dimension_items(target: dict[str, Any], source: dict[str, Any]) -> None:
    """合并维度候选，重名实体时追加候选值。"""
    for key, value in (source or {}).items():
        if key not in target:
            target[key] = value
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key].extend(value)
