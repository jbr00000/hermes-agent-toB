"""result 阶段 = lone-ai phase9 + 格式化：结果后处理 → 维度值映射 → LLM 格式化 → 绘图抽取。

失败路径（4 次重试耗尽）也在这里收口：``failed_status`` / ``failure_message`` /
``failure_format_output`` 产出用户可读的 failed 结果卡。
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from server.nl2sql import store

from ..llm import count_tokens
from ..prompts import PromptBuilder
from ..services import _parse_result_json

if TYPE_CHECKING:
    from ..orchestrator import Nl2sqlOrchestrator

logger = logging.getLogger(__name__)

_PIE_KEYWORDS = ("分布", "占比", "构成", "比例")
_FIGURE_TYPES = {"pie", "bar", "line"}


# ------------------------------------------------------------------ 阶段入口


async def run_single(
    orch: "Nl2sqlOrchestrator",
    question: str,
    phase_result: dict[str, Any],
    dataset_id: str,
) -> dict[str, Any]:
    """单数据集 result：失败则收口为 failed 结果卡；成功则格式化 + 数量字段清理。"""
    orch._phase_start("result")
    failed = failed_status(phase_result)
    if failed:
        error = failure_message(phase_result, failed)
        phase_result["sql_content"] = error
        format_output = failure_format_output(question)
        orch._phase_done("result", error=error)
        return orch._build_return(
            question, phase_result, [format_output], status="failed", error=error
        )

    value_mapping_context = build_value_mapping_context([dataset_id], phase_result.get("sql_content", ""))
    format_output = await format_final_response(
        orch,
        question=question,
        sql=phase_result.get("sql_content", ""),
        data=phase_result.get("exec_result", []),
        dataset_ids=[dataset_id],
        temperature=0.1,
        value_mapping_context=value_mapping_context,
    )
    if phase_result.get("exec_result"):
        quantity_analysis = await orch.post_process.analyze_removable_quantity_fields(
            question=question,
            sql_content=phase_result.get("sql_content", ""),
            exec_result=phase_result.get("exec_result", []),
        )
        format_output = orch.post_process.cleanup_format_output_quantity_fields(
            format_output, quantity_analysis
        )
    orch._phase_done("result", result_desc=format_output.get("result_desc", ""))
    return orch._build_return(question, phase_result, [format_output])


async def run_cross(
    orch: "Nl2sqlOrchestrator",
    question: str,
    phase_result: dict[str, Any],
    dataset_ids: list[str],
) -> dict[str, Any]:
    """跨数据集 result：phase9 后处理（依赖分析→字段拆分）后按子问题逐卡格式化。"""
    orch._phase_start("result")
    failed = failed_status(phase_result)
    if failed:
        error = failure_message(phase_result, failed)
        phase_result["sql_content"] = error
        phase_result["format_outputs"] = []
        orch._phase_done("result", error=error)
        return orch._build_return(question, phase_result, [], status="failed", error=error)

    phase_result = await post_process_cross(orch, phase_result)
    value_mapping_context = build_value_mapping_context(dataset_ids, phase_result.get("sql_content", ""))

    format_outputs: list[dict[str, Any]] = []
    quantity_analysis = phase_result.get("post_process_analysis", {}).get("removable_quantity_analysis", {})
    keep_fields = orch.post_process._get_explicit_quantity_fields(quantity_analysis)
    if phase_result.get("format_source") == "split":
        for part in phase_result.get("exec_result_split", []):
            part_output = await format_final_response(
                orch,
                question=str(part.get("question") or question),
                sql=phase_result.get("sql_content", ""),
                data=part.get("rows", []),
                dataset_ids=dataset_ids,
                temperature=0.1,
                value_mapping_context=value_mapping_context,
            )
            part_output = orch.post_process.cleanup_format_output_quantity_fields(
                part_output, quantity_analysis, keep_fields=keep_fields
            )
            format_outputs.append(part_output)
    else:
        format_output = await format_final_response(
            orch,
            question=question,
            sql=phase_result.get("sql_content", ""),
            data=phase_result.get("exec_result", []),
            dataset_ids=dataset_ids,
            temperature=0.1,
            value_mapping_context=value_mapping_context,
        )
        format_output = orch.post_process.cleanup_format_output_quantity_fields(
            format_output, quantity_analysis, keep_fields=keep_fields
        )
        format_outputs.append(format_output)
    orch._phase_done("result", result_desc=[item.get("result_desc", "") for item in format_outputs])
    return orch._build_return(question, phase_result, format_outputs)


# ------------------------------------------------------------------ 跨数据集后处理（phase9）


async def post_process_cross(
    orch: "Nl2sqlOrchestrator", phase_result: dict[str, Any]
) -> dict[str, Any]:
    """复合问题结果后处理：依赖分析 → 字段拆分 → 选择最终结果来源。"""
    phase_result.setdefault("post_process_analysis", {})
    phase_result.setdefault("exec_result_split", [])
    phase_result.setdefault("final_exec_result", phase_result.get("exec_result", []))
    phase_result.setdefault("format_source", "original")
    if phase_result.get("sql_exec_status") != "success":
        return phase_result

    question = phase_result.get("clarified_question") or ""
    sql_content = phase_result.get("sql_content", "")
    exec_result = phase_result.get("exec_result", [])
    try:
        analysis = await orch.post_process.analyze_dependency_structure(
            question=question, sql_content=sql_content, exec_result=exec_result
        )
        quantity_analysis = await orch.post_process.analyze_removable_quantity_fields(
            question=question, sql_content=sql_content, exec_result=exec_result
        )
        analysis["removable_quantity_analysis"] = quantity_analysis
        has_dependency = bool(analysis.get("has_dependency"))
        if has_dependency:
            split_result = orch.post_process.split_dependency_removable_quantity_columns(
                exec_result, question, quantity_analysis
            )
        else:
            split_result = orch.post_process.split_exec_result(exec_result, analysis)
        final_exec_result = orch.post_process.choose_final_exec_result(
            exec_result,
            {"has_dependency": False} if has_dependency and split_result else analysis,
            split_result,
        )
        phase_result["post_process_analysis"] = analysis
        phase_result["exec_result_split"] = split_result if (not has_dependency or split_result) else []
        phase_result["final_exec_result"] = final_exec_result
        phase_result["format_source"] = "original" if has_dependency and not split_result else "split"
    except Exception as exc:
        logger.warning("跨数据集结果后处理失败，降级使用原始结果: %s", exc, exc_info=True)
        phase_result["final_exec_result"] = exec_result
        phase_result["format_source"] = "original"
    return phase_result


# ------------------------------------------------------------------ 结果格式化


async def format_final_response(
    orch: "Nl2sqlOrchestrator",
    *,
    question: str,
    sql: str,
    data: list[dict[str, Any]],
    dataset_ids: list[str],
    temperature: float = 0.1,
    value_mapping_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """格式化最终响应（lone-ai _format_final_response 的 page 分支）。"""
    token_num = 0

    if not data:
        result_desc = "数据库中无相关数据"
        try:
            system_prompt = "你是专业的数据查询结果表达助手，请用自然、友好的中文解释空查询结果。"
            user_prompt = PromptBuilder.build_empty_result_rewrite_prompt(question=question)
            token_num += count_tokens(system_prompt) + count_tokens(user_prompt)
            response = await orch.llm.chat_completion(
                user_prompt=user_prompt, system_prompt=system_prompt, temperature=temperature
            )
            token_num += count_tokens(response)
            rewrite = _parse_result_json(response)
            if isinstance(rewrite, dict):
                result_desc = str(rewrite.get("result_desc") or result_desc).strip()
        except Exception as exc:
            logger.warning("空结果友好提示生成失败，使用默认提示: %s", exc)
        orch._token_total += token_num
        return {
            "type": "text", "title": question, "dimensions": "", "metrics": "",
            "data": [], "data_figure": [], "data_all": 0, "chunk_flag": "",
            "result_desc": result_desc, "content_desc": "", "figure_type": "text",
            "token_num": token_num,
        }

    try:
        value_mapping_context = value_mapping_context or build_value_mapping_context(dataset_ids, sql)
        apply_value_mapping_to_data(data, value_mapping_context)

        keys = list(data[0].keys()) if data else []
        if len(data) == 1 and len(keys) == 1:
            result_desc = f"查询结果为{data[0][keys[0]]}"
        else:
            result_desc = f"查询到{len(data)}条数据"
        chunk_flag = "数据量多于15条，从第15条开始的数据已合并为其他" if len(data) > 15 else ""

        # LLM 结果格式化（lone-ai 这里按表名追加的水项目口径已删除）
        system_prompt = "你是一个数据库专家，请按照要求格式化查询结果。"
        user_prompt = PromptBuilder.build_result_format_prompt(question=question, data=data, sql=sql)
        token_num += count_tokens(system_prompt) + count_tokens(user_prompt)
        response = await orch.llm.chat_completion(
            user_prompt=user_prompt, system_prompt=system_prompt, temperature=temperature
        )
        token_num += count_tokens(response)
        try:
            result: dict[str, Any] = _parse_result_json(response)
            if not isinstance(result, dict):
                raise ValueError("结果格式化输出不是 JSON 对象")
        except Exception:
            result = {"type": "text", "figure_type": "text", "dimensions": "", "metrics": "", "content_desc": ""}

        figure_result = build_figure_data_from_result(data, result, question)
        result["type"] = "text"
        result["figure_type"] = figure_result["figure_type"]
        result["dimensions"] = figure_result["dimensions"]
        result["metrics"] = figure_result["metrics"]
        result["data"] = data
        result["data_figure"] = figure_result["data_figure"]
        result["content_desc"] = str(result.get("content_desc") or "")
        result["data_all"] = len(data)
        result["chunk_flag"] = chunk_flag
        result["result_desc"] = result_desc
        result["title"] = question
        result["token_num"] = token_num
        orch._token_total += token_num
        return result
    except Exception as exc:
        logger.warning("格式化输出失败: %s", exc, exc_info=True)
        orch._token_total += token_num
        return {
            "type": "text", "title": question, "dimensions": "", "metrics": "",
            "data": data, "data_figure": [], "data_all": len(data), "chunk_flag": "",
            "result_desc": f"查询到{len(data)}条数据", "content_desc": "",
            "figure_type": "text", "token_num": token_num,
        }


# ------------------------------------------------------------------ 维度值映射


def build_value_mapping_context(dataset_ids: list[str], sql: str) -> dict[str, Any]:
    """维度码表 key→value 映射 + SQL AS 别名→列名映射（lone-ai 的 '1-八贯通线' 特例已删）。"""
    default_mappings_dict = store.get_dimension_value_mappings(dataset_ids)

    sql_mapping: dict[str, str] = {}
    normalized_sql = str(sql or "").replace("AS", "as").replace("As", "as").replace("aS", "as")
    for ele in re.split(r",|FROM", normalized_sql, flags=re.IGNORECASE):
        candidate_mapping = re.split(r" as ", ele)
        if len(candidate_mapping) == 2:
            column, ascontent = candidate_mapping
            if "." in column:
                column = column.strip().split(".")[-1]
            else:
                column = column.strip().split(" ")[-1]
            sql_mapping[ascontent.strip().replace('"', "")] = column.lower()
    return {"default_mappings_dict": default_mappings_dict, "sql_mapping": sql_mapping}


def apply_value_mapping_to_data(
    data: list[dict[str, Any]], value_mapping_context: Optional[dict[str, Any]]
) -> None:
    """把结果里的码表 key 原地替换为展示 value（直查列名，或经 SQL 别名间接查）。"""
    if not value_mapping_context:
        return
    default_mappings_dict = value_mapping_context.get("default_mappings_dict", {})
    sql_mapping = value_mapping_context.get("sql_mapping", {})
    if not isinstance(default_mappings_dict, dict) or not isinstance(sql_mapping, dict):
        return

    def get_mapping_value(mapping_items: dict[str, Any], value: Any) -> Optional[Any]:
        """兼容数据库返回数字和映射字典字符串 key 不一致的情况。"""
        if value in mapping_items:
            return mapping_items[value]
        string_value = str(value)
        if string_value in mapping_items:
            return mapping_items[string_value]
        return None

    for ele in data:
        if not isinstance(ele, dict):
            continue
        for key, value in ele.items():
            if key in default_mappings_dict:
                mapped = get_mapping_value(default_mappings_dict[key], value)
                if mapped is not None:
                    ele[key] = mapped
            elif sql_mapping.get(key) and sql_mapping[key] in default_mappings_dict:
                mapped = get_mapping_value(default_mappings_dict[sql_mapping[key]], value)
                if mapped is not None:
                    ele[key] = mapped


# ------------------------------------------------------------------ 绘图数据抽取


def _pick_single_field_name(field_text: Any) -> str:
    """从模型输出的字段文本中提取单个字段名。"""
    if field_text is None:
        return ""
    candidates = re.split(r"[,，、]", str(field_text))
    return candidates[0].strip() if candidates else ""


def build_figure_data_from_result(
    data: list[dict[str, Any]], result: dict[str, Any], question: str
) -> dict[str, Any]:
    """根据模型建议从全量数据中抽取两列绘图数据。"""
    empty = {"figure_type": "text", "dimensions": "", "metrics": "", "data_figure": []}
    figure_type = str(result.get("figure_type") or result.get("type") or "text").strip().lower()
    if figure_type not in _FIGURE_TYPES:
        return empty
    if figure_type == "pie" and not any(keyword in question for keyword in _PIE_KEYWORDS):
        return empty

    dimensions = _pick_single_field_name(result.get("dimensions"))
    metrics = _pick_single_field_name(result.get("metrics"))
    if not dimensions or not metrics or dimensions == metrics or len(data) <= 1:
        return empty

    data_figure = []
    for row in data:
        if not isinstance(row, dict) or dimensions not in row or metrics not in row:
            return empty
        data_figure.append({dimensions: row.get(dimensions), metrics: row.get(metrics)})
    return {
        "figure_type": figure_type,
        "dimensions": dimensions,
        "metrics": metrics,
        "data_figure": data_figure,
    }


# ------------------------------------------------------------------ 失败收口


def failed_status(phase_result: dict[str, Any]) -> str:
    for key in ("sql_gen_status", "sql_check_status", "sql_exec_status"):
        if phase_result.get(key) == "error":
            return key
    return ""


def failure_message(phase_result: dict[str, Any], failed: str) -> str:
    """4 次重试耗尽后的用户可读错误（lone-ai 口径，去掉 test_llm_connection 探测）。"""
    if failed == "sql_gen_status":
        return "SQL生成发生未知错误，请重新尝试"
    if failed == "sql_check_status":
        info = phase_result.get("sql_check_error_information") or []
        if info:
            return str(info[0].get("content") or "SQL安全检查失败")
        return "SQL安全检查失败"
    return "SQL执行失败"


def failure_format_output(question: str) -> dict[str, Any]:
    return {
        "type": "", "title": question, "dimensions": "", "metrics": "",
        "data": [], "data_figure": [], "data_all": 0, "chunk_flag": "",
        "result_desc": "查询失败", "content_desc": "", "figure_type": "text",
        "token_num": 0,
    }
