"""问数主编排器 —— lone-ai ``app/nl2sql_api.py`` NL2SQLOrchestrator 的裁剪移植。

三阶段实现已按阶段拆到 ``stages/`` 子包（understand / generate / result，
阶段映射与各阶段内逻辑见各模块 docstring）；本类只做：
  - 服务装配（LLMClient + 9 个阶段服务）
  - 事件回调（phase / sql）
  - 数据集/数据源解析与单/跨数据集流程编排
  - 统一返回组装（``_build_return``）

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

import logging
from typing import Any, Callable, Optional

from server.nl2sql import store

from . import Nl2sqlError
from .llm import LLMClient
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
)
from .stages import generate as generate_stage
from .stages import result as result_stage
from .stages import understand as understand_stage

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], None]


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

        not_select = await understand_stage.run_single(self, phase_result, question, dataset_id)

        self._phase_start("generate")
        # phase5 问题分解已删除：单数据集 steps=None（prompt 自动跳过分解段）
        selected_tables = await generate_stage.prepare_single(
            self, phase_result, question, dataset, dataset_id, not_select
        )
        phase_result = await generate_stage.generate_and_execute(self, phase_result, execution, db_type)
        self._phase_done(
            "generate",
            tables=selected_tables,
            rows=len(phase_result.get("exec_result") or []),
            attempts=phase_result.get("attempts", 1),
        )

        return await result_stage.run_single(self, question, phase_result, dataset_id)

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

        await understand_stage.run_cross(self, phase_result, question, contexts)

        self._phase_start("generate")
        # 阶段5：复合问题不分解，完整问题作为单一步骤
        phase_result["query_steps"] = [
            {"step": 1, "query": question, "dataset_ids": dataset_ids}
        ]
        combined_tables = await generate_stage.prepare_cross(self, phase_result, question, contexts)
        phase_result = await generate_stage.generate_and_execute(self, phase_result, execution, db_type)
        self._phase_done(
            "generate",
            tables=combined_tables,
            rows=len(phase_result.get("exec_result") or []),
            attempts=phase_result.get("attempts", 1),
        )

        return await result_stage.run_cross(self, question, phase_result, dataset_ids)

    # ------------------------------------------------------------------ 小工具

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
