"""问数主编排器 —— lone-ai ``app/nl2sql_api.py`` NL2SQLOrchestrator 的裁剪移植。

阶段映射（对应前端三段式折叠卡片）：
  understand = lone-ai phase1-4（保持原问题 → 实体抽取 → 候选匹配 → 不澄清整理）
  generate   = lone-ai phase6-8（三路召回 → 选表/表分析 → SQL生成+安检+执行，4 次重试）
  result     = lone-ai phase9 + 格式化（跨数据集结果后处理 → 维度值映射 → LLM 格式化）

相对 lone-ai 的裁剪：
  - Redis 结果缓存、短期记忆写回、clarify 续轮 —— 删除（无状态单次问答）
  - 澄清（phase4 强制不澄清分支）、问题分解（phase5 跳过，单数据集 steps=None）
  - test_llm_connection 探测、bhreason 驳回原因映射 —— 删除
  - CROSS_DATASET_REQUIRED_TABLES / CROSS_DATASET_JOIN_INSTRUCTION 水项目硬编码 —— 删除
    （跨数据集 required_tables 恒为 []，join 提示只保留各数据集内部关联）
  - request_method!="page" 分支 —— 删除（本仓只有 page 一种消费方式）
  - lone-ai 的 data_display 截断表是死代码（算完没用，page 分支始终返回全量 data），
    这里只保留 chunk_flag 标记，不复制死代码

事件回调：``emit(event, payload)``，event ∈ phase / sql；每次 ask 的最终结果
由返回值带出（路由层负责转成 done/error 事件）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import Any, Callable, Optional

from server.nl2sql import store

from . import Nl2sqlError
from .llm import LLMClient, count_tokens
from .prompts import PromptBuilder
from .services import (
    EntityCandidateService,
    EntityExtractionService,
    InformationRetrievalService,
    SchemaSelectionService,
    SecurityCheckService,
    SQLExecutionService,
    SQLGenerationService,
    SQLResultPostProcessService,
    TableAnalysisService,
    _parse_result_json,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], None]

_MAX_ATTEMPTS = 4  # lone-ai：SQL 生成+执行共享 4 次尝试
_PIE_KEYWORDS = ("分布", "占比", "构成", "比例")
_FIGURE_TYPES = {"pie", "bar", "line"}


class Nl2sqlOrchestrator:
    """问数主编排器（每次问数请求新建一个实例，实例不可复用）。"""

    def __init__(self, emit: Optional[EmitFn] = None) -> None:
        self._emit_cb = emit
        self._token_total = 0
        self.llm = LLMClient()
        self.entity_extraction = EntityExtractionService(self.llm)
        self.entity_candidate = EntityCandidateService(self.llm)
        self.retrieval = InformationRetrievalService()
        self.schema_selection = SchemaSelectionService(self.llm)
        self.table_analysis = TableAnalysisService()
        self.sql_generation = SQLGenerationService(self.llm)
        self.security_check = SecurityCheckService()
        self.post_process = SQLResultPostProcessService(self.llm)

    # ------------------------------------------------------------------ 事件

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._emit_cb is None:
            return
        try:
            self._emit_cb(event, payload)
        except Exception as exc:  # 回调故障不中断主链路
            logger.warning("问数事件回调失败(%s): %s", event, exc)

    def _phase_start(self, step: str) -> None:
        self._emit("phase", {"step": step, "status": "start"})

    def _phase_done(self, step: str, **payload: Any) -> None:
        self._emit("phase", {"step": step, "status": "done", **payload})

    # ------------------------------------------------------------------ 入口

    async def ask(
        self,
        question: str,
        dataset_id: str | None = None,
        dataset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """问数主入口。跨数据集（dataset_ids ≥ 2）走复合流，否则走单数据集流。

        返回统一契约::

            {
              "question": str, "sql_content": str, "explain_content": str,
              "status": "success" | "failed",   # failed = 4 次重试耗尽
              "error": str | None,              # failed 时的用户可读原因
              "format_outputs": [格式化结果卡, ...],  # 单数据集也是单元素数组
              "token_num": int,
            }

        配置/数据源类问题抛 Nl2sqlError（路由层转 error 事件）。
        """
        question = (question or "").strip()
        if not question:
            raise Nl2sqlError("问题不能为空，请重新输入")

        ids = [str(item).strip() for item in (dataset_ids or []) if str(item).strip()]
        ids = list(dict.fromkeys(ids))
        if dataset_id and dataset_id.strip() and dataset_id.strip() not in ids:
            ids.insert(0, dataset_id.strip())
        if not ids:
            raise Nl2sqlError("请选择至少一个数据集")

        if len(ids) >= 2:
            return await self._ask_cross(question, ids)
        return await self._ask_single(question, ids[0])

    # ------------------------------------------------------------------ 单数据集

    async def _ask_single(self, question: str, dataset_id: str) -> dict[str, Any]:
        dataset, datasource = self._resolve_dataset_datasource(dataset_id)
        db_type = datasource.get("db_type") or "mysql"
        execution = SQLExecutionService(datasource)

        phase_result: dict[str, Any] = {"dataset_id": dataset_id, "query_steps": None}

        # ---------- understand：phase1-4 ----------
        self._phase_start("understand")
        # phase1：保持原问题（lone-ai 现行行为，不做改写）
        phase_result.update({
            "original_question": question,
            "rewrite_question1": question,
            "clarified_question": question,
            "is_followup": False,
            "followup_type": "",
            "followup_explain": "NL2SQL阶段一保持原问题进入后续阶段",
        })

        # phase2：实体抽取
        entity_output = await self.entity_extraction.extract_entities(question)
        self._token_total += entity_output.get("token_num", 0)
        time_entities = entity_output.get("time_entities", [])
        non_time_entities = entity_output.get("other_entities", [])
        metric_entities = entity_output.get("metric_entities", [])
        phase_result["time_entities"] = time_entities
        phase_result["non_time_entities"] = non_time_entities
        phase_result["metric_entities"] = metric_entities
        if not non_time_entities and not time_entities:
            phase_result["entity_select_explain"] = "当前查询中未识别出合适的实体"
        else:
            joined = "、".join(non_time_entities + time_entities + metric_entities)
            phase_result["entity_select_explain"] = f"查询中识别出以下实体：{joined}"

        # phase3：实体候选匹配
        candidate_result: dict[str, Any] = {}
        if non_time_entities:
            candidate_result = await self.entity_candidate.get_candidates(
                question, non_time_entities + metric_entities, dataset_id
            )
            self._token_total += candidate_result.pop("token_num", 0)
        phase_result["non_time_entities_candidate_result"] = candidate_result

        # phase4（强制不澄清）：剔除 *_ambiguity_level，全部候选进入 dimension
        not_select = {
            key: value for key, value in candidate_result.items()
            if not str(key).endswith("_ambiguity_level")
        }
        phase_result["non_time_entities_auto_clarify_result"] = {}
        phase_result["non_time_entities_not_select_result"] = not_select
        phase_result["clarify_items"] = []
        self._phase_done(
            "understand",
            question=question,
            entities={
                "time": time_entities,
                "other": non_time_entities,
                "metric": metric_entities,
            },
            entity_explain=phase_result["entity_select_explain"],
            candidates=not_select,
        )

        # ---------- generate：phase6-8 ----------
        self._phase_start("generate")
        # phase5 问题分解已删除：单数据集 steps=None（prompt 自动跳过分解段）

        # phase6：三路召回 + 全量选表 + 表分析
        retrieval_result = await self.retrieval.retrieve_all(question, dataset_id)
        phase_result["few_shots"] = retrieval_result["qa_pair"]
        phase_result["index"] = retrieval_result["index"]
        phase_result["terminology"] = retrieval_result["terminology"]
        phase_result["dimension"] = dict(not_select)

        selected_tables, table_ddls = await self.schema_selection.select_schema(dataset_id)
        phase_result["selected_tables"] = selected_tables
        phase_result["table_ddls"] = table_ddls
        if not table_ddls:
            raise Nl2sqlError(f"数据集「{dataset['name']}」没有已启用的表结构，请先在元数据配置中启用")

        _is_multitable, join_path = self.table_analysis.analyze_tables(selected_tables, dataset_id)
        phase_result["join_path"] = join_path

        # phase7+8：生成/安检/执行，共享 4 次重试
        phase_result = await self._generate_and_execute(phase_result, execution, db_type)
        self._phase_done(
            "generate",
            tables=selected_tables,
            rows=len(phase_result.get("exec_result") or []),
            attempts=phase_result.get("attempts", 1),
        )

        # ---------- result：格式化 ----------
        self._phase_start("result")
        failed_status = self._failed_status(phase_result)
        if failed_status:
            error = self._failure_message(phase_result, failed_status)
            phase_result["sql_content"] = error
            format_output = self._failure_format_output(question)
            self._phase_done("result", error=error)
            return self._build_return(
                question, phase_result, [format_output], status="failed", error=error
            )

        value_mapping_context = self._build_value_mapping_context([dataset_id], phase_result.get("sql_content", ""))
        format_output = await self._format_final_response(
            question=question,
            sql=phase_result.get("sql_content", ""),
            data=phase_result.get("exec_result", []),
            dataset_ids=[dataset_id],
            temperature=0.1,
            value_mapping_context=value_mapping_context,
        )
        if phase_result.get("exec_result"):
            quantity_analysis = await self.post_process.analyze_removable_quantity_fields(
                question=question,
                sql_content=phase_result.get("sql_content", ""),
                exec_result=phase_result.get("exec_result", []),
            )
            format_output = self.post_process.cleanup_format_output_quantity_fields(
                format_output, quantity_analysis
            )
        self._phase_done("result", result_desc=format_output.get("result_desc", ""))
        return self._build_return(question, phase_result, [format_output])

    # ------------------------------------------------------------------ 跨数据集

    async def _ask_cross(self, question: str, dataset_ids: list[str]) -> dict[str, Any]:
        datasets = []
        datasources = []
        for ds_id in dataset_ids:
            dataset, datasource = self._resolve_dataset_datasource(ds_id)
            datasets.append(dataset)
            datasources.append(datasource)
        datasource_ids = {ds["id"] for ds in datasources}
        if len(datasource_ids) > 1:
            names = "、".join(ds["name"] for ds in datasets)
            raise Nl2sqlError(f"跨数据集问数要求所选数据集挂在同一个数据源下（{names} 不在同一数据源）")
        db_type = datasources[0].get("db_type") or "mysql"
        execution = SQLExecutionService(datasources[0])

        contexts: list[dict[str, Any]] = [{"dataset_id": ds_id} for ds_id in dataset_ids]
        phase_result: dict[str, Any] = {
            "dataset_id": dataset_ids[0],
            "dataset_ids": dataset_ids,
            "original_question": question,
            "rewrite_question1": question,
            "clarified_question": question,
            "is_followup": False,
            "followup_type": "",
            "clarify_items": [],
        }

        # ---------- understand ----------
        self._phase_start("understand")
        # 阶段2：完整复合问题抽一次实体
        entity_output = await self.entity_extraction.extract_entities(question)
        self._token_total += entity_output.get("token_num", 0)
        time_entities = entity_output.get("time_entities", [])
        non_time_entities = entity_output.get("other_entities", [])
        metric_entities = entity_output.get("metric_entities", [])
        phase_result["time_entities"] = time_entities
        phase_result["non_time_entities"] = non_time_entities
        phase_result["metric_entities"] = metric_entities
        joined = "、".join(non_time_entities + time_entities + metric_entities)
        phase_result["entity_select_explain"] = (
            f"查询中识别出以下实体：{joined}" if joined else "当前查询中未识别出合适的实体"
        )

        # 阶段3：实体在每个数据集下分别做候选匹配后合并（候选拼接，ambiguity 取 min）
        merged_candidates: dict[str, Any] = {}
        for context in contexts:
            candidate_result: dict[str, Any] = {}
            if non_time_entities:
                candidate_result = await self.entity_candidate.get_candidates(
                    question, non_time_entities + metric_entities, context["dataset_id"]
                )
                self._token_total += candidate_result.pop("token_num", 0)
            context["dimension"] = {
                key: copy.deepcopy(value)
                for key, value in candidate_result.items()
                if not str(key).endswith("_ambiguity_level")
            }
            for key, value in candidate_result.items():
                if key not in merged_candidates:
                    merged_candidates[key] = copy.deepcopy(value)
                elif isinstance(merged_candidates[key], list) and isinstance(value, list):
                    merged_candidates[key] = merged_candidates[key] + copy.deepcopy(value)
                elif str(key).endswith("_ambiguity_level"):
                    merged_candidates[key] = min(float(merged_candidates[key]), float(value))

        not_select = {
            key: value for key, value in merged_candidates.items()
            if not str(key).endswith("_ambiguity_level")
        }
        phase_result["non_time_entities_candidate_result"] = merged_candidates
        phase_result["non_time_entities_auto_clarify_result"] = {}
        phase_result["non_time_entities_not_select_result"] = not_select
        self._phase_done(
            "understand",
            question=question,
            entities={"time": time_entities, "other": non_time_entities, "metric": metric_entities},
            entity_explain=phase_result["entity_select_explain"],
            candidates=not_select,
        )

        # ---------- generate ----------
        self._phase_start("generate")
        # 阶段5：复合问题不分解，完整问题作为单一步骤
        phase_result["query_steps"] = [
            {"step": 1, "query": question, "dataset_ids": dataset_ids}
        ]

        # 阶段6：每数据集召回 + LLM 筛表 + 表分析，合并去重
        combined_few_shots: list[dict] = []
        combined_index: list[dict] = []
        combined_terminology: list[dict] = []
        combined_dimension: dict[str, Any] = {}
        combined_tables: list[str] = []
        combined_ddls: list[str] = []
        join_paths: list[str] = []

        for context in contexts:
            ds_id = context["dataset_id"]
            retrieval_result = await self.retrieval.retrieve_all(question, ds_id)
            context["terminology"] = retrieval_result["terminology"]

            selected_tables, table_ddls = await self.schema_selection.select_schema_for_cross_dataset(
                question,
                context.get("dimension", {}),
                context["terminology"],
                ds_id,
                required_tables=[],  # lone-ai 的水项目必选表已删除
            )
            context["selected_tables"] = selected_tables
            context["table_ddls"] = table_ddls
            _is_multitable, join_path = self.table_analysis.analyze_tables(selected_tables, ds_id)
            context["join_path"] = join_path

            self._merge_retrieval_items(combined_few_shots, retrieval_result["qa_pair"])
            self._merge_retrieval_items(combined_index, retrieval_result["index"])
            self._merge_retrieval_items(combined_terminology, retrieval_result["terminology"])
            self._merge_dimension_items(combined_dimension, context.get("dimension", {}))
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

        # 阶段7+8 重试环
        phase_result = await self._generate_and_execute(phase_result, execution, db_type)
        self._phase_done(
            "generate",
            tables=combined_tables,
            rows=len(phase_result.get("exec_result") or []),
            attempts=phase_result.get("attempts", 1),
        )

        # ---------- result：phase9 后处理 + 格式化 ----------
        self._phase_start("result")
        failed_status = self._failed_status(phase_result)
        if failed_status:
            error = self._failure_message(phase_result, failed_status)
            phase_result["sql_content"] = error
            phase_result["format_outputs"] = []
            self._phase_done("result", error=error)
            return self._build_return(question, phase_result, [], status="failed", error=error)

        phase_result = await self._phase9_post_process(phase_result)
        value_mapping_context = self._build_value_mapping_context(dataset_ids, phase_result.get("sql_content", ""))

        format_outputs: list[dict[str, Any]] = []
        quantity_analysis = phase_result.get("post_process_analysis", {}).get("removable_quantity_analysis", {})
        keep_fields = self.post_process._get_explicit_quantity_fields(quantity_analysis)
        if phase_result.get("format_source") == "split":
            for part in phase_result.get("exec_result_split", []):
                part_output = await self._format_final_response(
                    question=str(part.get("question") or question),
                    sql=phase_result.get("sql_content", ""),
                    data=part.get("rows", []),
                    dataset_ids=dataset_ids,
                    temperature=0.1,
                    value_mapping_context=value_mapping_context,
                )
                part_output = self.post_process.cleanup_format_output_quantity_fields(
                    part_output, quantity_analysis, keep_fields=keep_fields
                )
                format_outputs.append(part_output)
        else:
            format_output = await self._format_final_response(
                question=question,
                sql=phase_result.get("sql_content", ""),
                data=phase_result.get("exec_result", []),
                dataset_ids=dataset_ids,
                temperature=0.1,
                value_mapping_context=value_mapping_context,
            )
            format_output = self.post_process.cleanup_format_output_quantity_fields(
                format_output, quantity_analysis, keep_fields=keep_fields
            )
            format_outputs.append(format_output)
        self._phase_done("result", result_desc=[item.get("result_desc", "") for item in format_outputs])
        return self._build_return(question, phase_result, format_outputs)

    # ------------------------------------------------------------------ 生成+执行重试环

    async def _generate_and_execute(
        self,
        phase_result: dict[str, Any],
        execution: SQLExecutionService,
        db_type: str,
    ) -> dict[str, Any]:
        """lone-ai phase7+8：SQL 生成/安检/执行共享 4 次尝试，错误累积进上下文。"""
        attempt = 0
        exist_error_information: list[dict[str, str]] = []
        while attempt < _MAX_ATTEMPTS:
            phase_result = await self.sql_generation.generate_sql(
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
            self._token_total += phase_result.get("sql_gen_tokens", 0)

            if phase_result.get("sql_gen_status") == "error":
                attempt += 1
                exist_error_information.extend(phase_result.get("sql_gen_error_information", []))
                logger.info("问数第 %s 次尝试 SQL 生成失败", attempt)
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(1)
                continue

            self._replace_tail_limit_one(phase_result)
            is_passed, blocked_reason, _warnings = self.security_check.check_security(
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
            self._emit("sql", {
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

    # ------------------------------------------------------------------ 跨数据集后处理（phase9）

    async def _phase9_post_process(self, phase_result: dict[str, Any]) -> dict[str, Any]:
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
            analysis = await self.post_process.analyze_dependency_structure(
                question=question, sql_content=sql_content, exec_result=exec_result
            )
            quantity_analysis = await self.post_process.analyze_removable_quantity_fields(
                question=question, sql_content=sql_content, exec_result=exec_result
            )
            analysis["removable_quantity_analysis"] = quantity_analysis
            has_dependency = bool(analysis.get("has_dependency"))
            if has_dependency:
                split_result = self.post_process.split_dependency_removable_quantity_columns(
                    exec_result, question, quantity_analysis
                )
            else:
                split_result = self.post_process.split_exec_result(exec_result, analysis)
            final_exec_result = self.post_process.choose_final_exec_result(
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

    async def _format_final_response(
        self,
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
                response = await self.llm.chat_completion(
                    user_prompt=user_prompt, system_prompt=system_prompt, temperature=temperature
                )
                token_num += count_tokens(response)
                rewrite = _parse_result_json(response)
                if isinstance(rewrite, dict):
                    result_desc = str(rewrite.get("result_desc") or result_desc).strip()
            except Exception as exc:
                logger.warning("空结果友好提示生成失败，使用默认提示: %s", exc)
            self._token_total += token_num
            return {
                "type": "text", "title": question, "dimensions": "", "metrics": "",
                "data": [], "data_figure": [], "data_all": 0, "chunk_flag": "",
                "result_desc": result_desc, "content_desc": "", "figure_type": "text",
                "token_num": token_num,
            }

        try:
            value_mapping_context = value_mapping_context or self._build_value_mapping_context(dataset_ids, sql)
            self._apply_value_mapping_to_data(data, value_mapping_context)

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
            response = await self.llm.chat_completion(
                user_prompt=user_prompt, system_prompt=system_prompt, temperature=temperature
            )
            token_num += count_tokens(response)
            try:
                result: dict[str, Any] = _parse_result_json(response)
                if not isinstance(result, dict):
                    raise ValueError("结果格式化输出不是 JSON 对象")
            except Exception:
                result = {"type": "text", "figure_type": "text", "dimensions": "", "metrics": "", "content_desc": ""}

            figure_result = self._build_figure_data_from_result(data, result, question)
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
            self._token_total += token_num
            return result
        except Exception as exc:
            logger.warning("格式化输出失败: %s", exc, exc_info=True)
            self._token_total += token_num
            return {
                "type": "text", "title": question, "dimensions": "", "metrics": "",
                "data": data, "data_figure": [], "data_all": len(data), "chunk_flag": "",
                "result_desc": f"查询到{len(data)}条数据", "content_desc": "",
                "figure_type": "text", "token_num": token_num,
            }

    # ------------------------------------------------------------------ 维度值映射

    @staticmethod
    def _build_value_mapping_context(dataset_ids: list[str], sql: str) -> dict[str, Any]:
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

    @staticmethod
    def _apply_value_mapping_to_data(
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

    @staticmethod
    def _pick_single_field_name(field_text: Any) -> str:
        """从模型输出的字段文本中提取单个字段名。"""
        if field_text is None:
            return ""
        candidates = re.split(r"[,，、]", str(field_text))
        return candidates[0].strip() if candidates else ""

    @classmethod
    def _build_figure_data_from_result(
        cls, data: list[dict[str, Any]], result: dict[str, Any], question: str
    ) -> dict[str, Any]:
        """根据模型建议从全量数据中抽取两列绘图数据。"""
        empty = {"figure_type": "text", "dimensions": "", "metrics": "", "data_figure": []}
        figure_type = str(result.get("figure_type") or result.get("type") or "text").strip().lower()
        if figure_type not in _FIGURE_TYPES:
            return empty
        if figure_type == "pie" and not any(keyword in question for keyword in _PIE_KEYWORDS):
            return empty

        dimensions = cls._pick_single_field_name(result.get("dimensions"))
        metrics = cls._pick_single_field_name(result.get("metrics"))
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

    # ------------------------------------------------------------------ 小工具

    @staticmethod
    def _replace_tail_limit_one(phase_result: dict[str, Any]) -> None:
        """SQL 执行前仅处理末尾 limit 1（避免误改子查询/CTE 里的 limit 1）。"""
        sql_content = str(phase_result.get("sql_content") or "")
        tail_text = sql_content[-10:]
        if not re.search(r"\blimit 1\b", tail_text, flags=re.IGNORECASE):
            return
        new_tail = re.sub(r"\blimit 1\b", "limit 10", tail_text, count=1, flags=re.IGNORECASE)
        phase_result["sql_content"] = f"{sql_content[:-10]}{new_tail}"
        logger.info("[SQL预处理] 检测到 SQL 末尾 limit 1，已替换为 limit 10")

    @staticmethod
    def _merge_retrieval_items(target: list[dict[str, Any]], source: list[dict[str, Any]]) -> None:
        """按内容去重合并召回信息。"""
        exists = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in target}
        for item in source or []:
            fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if fingerprint not in exists:
                target.append(item)
                exists.add(fingerprint)

    @staticmethod
    def _merge_dimension_items(target: dict[str, Any], source: dict[str, Any]) -> None:
        """合并维度候选，重名实体时追加候选值。"""
        for key, value in (source or {}).items():
            if key not in target:
                target[key] = value
            elif isinstance(target[key], list) and isinstance(value, list):
                target[key].extend(value)

    @staticmethod
    def _resolve_dataset_datasource(dataset_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        dataset = store.get_dataset(dataset_id)
        if dataset is None:
            raise Nl2sqlError("数据集不存在或已被删除")
        if not dataset.get("enabled", True):
            raise Nl2sqlError(f"数据集「{dataset['name']}」已停用")
        datasource = store.get_datasource(dataset["datasource_id"])
        if datasource is None:
            raise Nl2sqlError(f"数据集「{dataset['name']}」的数据源不存在")
        if not datasource.get("password_enc"):
            raise Nl2sqlError(f"数据源「{datasource['name']}」未配置密码")
        return dataset, datasource

    @staticmethod
    def _failed_status(phase_result: dict[str, Any]) -> str:
        for key in ("sql_gen_status", "sql_check_status", "sql_exec_status"):
            if phase_result.get(key) == "error":
                return key
        return ""

    @staticmethod
    def _failure_message(phase_result: dict[str, Any], failed_status: str) -> str:
        """4 次重试耗尽后的用户可读错误（lone-ai 口径，去掉 test_llm_connection 探测）。"""
        if failed_status == "sql_gen_status":
            return "SQL生成发生未知错误，请重新尝试"
        if failed_status == "sql_check_status":
            info = phase_result.get("sql_check_error_information") or []
            if info:
                return str(info[0].get("content") or "SQL安全检查失败")
            return "SQL安全检查失败"
        return "SQL执行失败"

    @staticmethod
    def _failure_format_output(question: str) -> dict[str, Any]:
        return {
            "type": "", "title": question, "dimensions": "", "metrics": "",
            "data": [], "data_figure": [], "data_all": 0, "chunk_flag": "",
            "result_desc": "查询失败", "content_desc": "", "figure_type": "text",
            "token_num": 0,
        }

    def _build_return(
        self,
        question: str,
        phase_result: dict[str, Any],
        format_outputs: list[dict[str, Any]],
        *,
        status: str = "success",
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "question": question,
            "sql_content": str(phase_result.get("sql_content") or ""),
            "explain_content": str(phase_result.get("explain_content") or ""),
            "status": status,
            "error": error,
            "format_outputs": format_outputs,
            "token_num": self._token_total,
        }
