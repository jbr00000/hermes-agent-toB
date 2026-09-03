"""understand 阶段 = lone-ai phase1-4：保持原问题 → 实体抽取 → 候选匹配 → 不澄清整理。

裁剪口径（与 lone-ai 一致）：phase1 不改写问题；phase4 强制不澄清——剔除
``*_ambiguity_level`` 后全部候选直接进入 dimension 上下文。
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..orchestrator import Nl2sqlOrchestrator


def _entity_explain(time_entities: list, non_time_entities: list, metric_entities: list) -> str:
    if not non_time_entities and not time_entities:
        return "当前查询中未识别出合适的实体"
    joined = "、".join(non_time_entities + time_entities + metric_entities)
    return f"查询中识别出以下实体：{joined}"


def _strip_ambiguity(candidate_result: dict[str, Any]) -> dict[str, Any]:
    """phase4（强制不澄清）：剔除 *_ambiguity_level，全部候选进入 dimension。"""
    return {
        key: value for key, value in candidate_result.items()
        if not str(key).endswith("_ambiguity_level")
    }


async def run_single(
    orch: "Nl2sqlOrchestrator",
    phase_result: dict[str, Any],
    question: str,
    dataset_id: str,
) -> dict[str, Any]:
    """单数据集 understand：实体抽取一次 + 候选匹配一次。返回剔除 ambiguity 后的候选。"""
    orch._phase_start("understand")
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
    entity_output = await orch.entity_extraction.extract_entities(question)
    orch._token_total += entity_output.get("token_num", 0)
    time_entities = entity_output.get("time_entities", [])
    non_time_entities = entity_output.get("other_entities", [])
    metric_entities = entity_output.get("metric_entities", [])
    phase_result["time_entities"] = time_entities
    phase_result["non_time_entities"] = non_time_entities
    phase_result["metric_entities"] = metric_entities
    phase_result["entity_select_explain"] = _entity_explain(time_entities, non_time_entities, metric_entities)

    # phase3：实体候选匹配
    candidate_result: dict[str, Any] = {}
    if non_time_entities:
        candidate_result = await orch.entity_candidate.get_candidates(
            question, non_time_entities + metric_entities, dataset_id
        )
        orch._token_total += candidate_result.pop("token_num", 0)
    phase_result["non_time_entities_candidate_result"] = candidate_result

    # phase4（强制不澄清）
    not_select = _strip_ambiguity(candidate_result)
    phase_result["non_time_entities_auto_clarify_result"] = {}
    phase_result["non_time_entities_not_select_result"] = not_select
    phase_result["clarify_items"] = []
    orch._phase_done(
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
    return not_select


async def run_cross(
    orch: "Nl2sqlOrchestrator",
    phase_result: dict[str, Any],
    question: str,
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """跨数据集 understand：完整复合问题抽一次实体，候选在每个数据集下分别匹配后合并。

    合并口径（lone-ai）：候选拼接去重交给下游，``*_ambiguity_level`` 取 min。
    每个 context 记下自己的 dimension 候选（供 generate 阶段按数据集筛表）。
    """
    orch._phase_start("understand")
    # 阶段2：完整复合问题抽一次实体
    entity_output = await orch.entity_extraction.extract_entities(question)
    orch._token_total += entity_output.get("token_num", 0)
    time_entities = entity_output.get("time_entities", [])
    non_time_entities = entity_output.get("other_entities", [])
    metric_entities = entity_output.get("metric_entities", [])
    phase_result["time_entities"] = time_entities
    phase_result["non_time_entities"] = non_time_entities
    phase_result["metric_entities"] = metric_entities
    phase_result["entity_select_explain"] = _entity_explain(time_entities, non_time_entities, metric_entities)

    # 阶段3：实体在每个数据集下分别做候选匹配后合并（候选拼接，ambiguity 取 min）
    merged_candidates: dict[str, Any] = {}
    for context in contexts:
        candidate_result: dict[str, Any] = {}
        if non_time_entities:
            candidate_result = await orch.entity_candidate.get_candidates(
                question, non_time_entities + metric_entities, context["dataset_id"]
            )
            orch._token_total += candidate_result.pop("token_num", 0)
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

    not_select = _strip_ambiguity(merged_candidates)
    phase_result["non_time_entities_candidate_result"] = merged_candidates
    phase_result["non_time_entities_auto_clarify_result"] = {}
    phase_result["non_time_entities_not_select_result"] = not_select
    orch._phase_done(
        "understand",
        question=question,
        entities={"time": time_entities, "other": non_time_entities, "metric": metric_entities},
        entity_explain=phase_result["entity_select_explain"],
        candidates=not_select,
    )
    return not_select
