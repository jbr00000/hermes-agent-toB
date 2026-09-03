"""问数流水线服务 —— 移植 lone-ai ``app/nl2sql_api.py`` 的九个服务类。

保留：实体抽取 / 实体候选匹配 / 信息召回 / Schema 选择 / 多表分析 /
SQL 生成 / 安全校验 / SQL 执行 / 结果后处理。
删除：ClarificationService、QueryDecompositionService（产品决策：不澄清不分解）。

关键适配：
  - 元数据读取走 server.nl2sql.store（SQLAlchemy，参数化；lone-ai 的
    TableAnalysisService 里 ``dataset_id = {dataset_id}`` 字符串插值已修掉）
  - hybrid_search 改接 algorithm.retrieval（server.knowledge 客户端）
  - LLM 输出解析统一 ``_parse_result_json``（<result> 标签 + json-repair）
  - lone-ai 的 milvus_config/index_params 两个透传参数删除（检索层自取配置）
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from json import loads
from typing import Any, Optional

from json_repair import repair_json

from server.nl2sql import store

from . import Nl2sqlError, business_db
from .business_db import MAX_RESULT_JSON_CHARS, TRUNCATED_ROWS
from .llm import LLMClient, count_tokens
from .prompts import PromptBuilder
from .retrieval import hybrid_search
from .sql_guard import SQLValidator

logger = logging.getLogger(__name__)

# 问数五类索引名（ES 索引与 Milvus collection 同名；阶段5 sync.py 按此建索引）
INDEX_NAMES = {
    "PROFESSIONAL_TERMINOLOGY": "nl2sql_business_professional_terminology",
    "DIMENSION": "nl2sql_business_dimension",
    "DDL_CHUNK": "nl2sql_business_ddl_chunk",
    "QA_PAIR": "nl2sql_business_qa_pair",
    "INDEX": "nl2sql_business_index",
}


def _parse_result_json(response: str) -> Any:
    """从 LLM 响应解析 <result> 标签内的 JSON（json-repair 兜底）。"""
    json_match = re.findall(r"<result>(.*)</result>", response, re.DOTALL)
    target = json_match[0] if json_match else response
    return loads(repair_json(target, ensure_ascii=False))


class EntityExtractionService:
    """实体抽取服务"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def extract_entities(self, question: str) -> dict[str, Any]:
        """从用户问题中提取实体；失败返回空列表（lone-ai 同口径）。"""
        total_tokens = 0
        try:
            prompt = PromptBuilder.build_entity_extraction_prompt(question)
            total_tokens += count_tokens(prompt)
            system_prompt = "你是一个实体抽取专家，精通各类实体的抽取"
            total_tokens += count_tokens(system_prompt)

            response = await self.llm_client.chat_completion(
                user_prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )
            total_tokens += count_tokens(response)

            result = _parse_result_json(response)
            result["token_num"] = total_tokens
            return result
        except Exception as exc:
            logger.warning("实体抽取失败: %s", exc, exc_info=True)
            return {
                "time_entities": [],
                "other_entities": [],
                "metric_entities": [],
                "token_num": total_tokens,
            }


class EntityCandidateService:
    """实体候选值匹配服务（维度码表 + DDL 字段两路召回，LLM 排序 top5）"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def get_candidates(
        self,
        question: str,
        entities: list[str],
        dataset_id: str,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> dict[str, Any]:
        """为每个实体并行获取候选值 → {entity: [候选...], "{entity}_ambiguity_level": x}"""
        results_all: dict[str, Any] = {"token_num": 0}
        try:
            async def single_entity_search(entity: str) -> dict[str, Any]:
                results: dict[str, Any] = {"token_num": 0}
                try:
                    dim_candidates = self._search_dimension(entity, dataset_id, top_k, rrf_k)
                    column_candidates = self._search_columns(entity, dataset_id, top_k, rrf_k)
                    all_candidates = dim_candidates + column_candidates
                    if all_candidates:
                        ranked = await self._rank_candidates(question, entity, all_candidates)
                        total_token = ranked["token_num"]
                        ambiguity_level = ranked["ambiguity_level"]
                        ranked_candidates = ranked["ranked_candidates"]
                    else:
                        ranked_candidates = []
                        total_token = 0
                        ambiguity_level = 0
                    results[entity] = ranked_candidates
                    results["token_num"] += total_token
                    results[f"{entity}_ambiguity_level"] = ambiguity_level
                    return results
                except Exception as exc:
                    logger.warning("获取实体 %s 候选值失败: %s", entity, exc, exc_info=True)
                    return results

            futures = await asyncio.gather(*[single_entity_search(e) for e in entities])
            for res in futures:
                token_num = res["token_num"]
                res.pop("token_num", None)
                results_all.update(res)
                results_all["token_num"] += token_num
            return results_all
        except Exception as exc:
            logger.warning("实体候选匹配失败: %s", exc, exc_info=True)
            return results_all

    def _search_dimension(
        self, entity: str, dataset_id: str, top_k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """从维度码表索引搜索标准值。"""
        try:
            chunks = hybrid_search(
                query=entity,
                collection_name=INDEX_NAMES["DIMENSION"],
                es_index_name=INDEX_NAMES["DIMENSION"],
                expr_columns={"dataset_id": [dataset_id]},
                search_fields=["db_data_value"],
                output_fields=["id", "dimension_name", "db_data_value", "db_data_key", "dimension_display_name"],
                top_k=top_k,
                rerank_way="rerank",
                rerank_columns=["db_data_value"],
                rrf_k=rrf_k,
            )
            candidates = []
            for chunk in chunks:
                dimension_name = chunk["source"]["dimension_name"]
                dimension_split = str(dimension_name).rsplit(".", 1)
                if len(dimension_split) != 2:
                    continue
                table_name, column_name = dimension_split
                if chunk["score"] > 0:
                    candidates.append({
                        "source": "数据库中标准值",
                        "key": chunk["source"]["db_data_key"],
                        "value": chunk["source"]["db_data_value"],
                        "table_name": table_name,
                        "column_name": column_name,
                        "dimension_display_name": chunk["source"]["dimension_display_name"],
                        "score": chunk["score"],
                    })
            return candidates
        except Exception as exc:
            logger.warning("维度索引搜索失败: %s", exc)
            return []

    def _search_columns(
        self, entity: str, dataset_id: str, top_k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """从 DDL 字段索引搜索字段注释匹配的列。"""
        try:
            chunks = hybrid_search(
                query=entity,
                collection_name=INDEX_NAMES["DDL_CHUNK"],
                es_index_name=INDEX_NAMES["DDL_CHUNK"],
                expr_columns={"dataset_id": [dataset_id]},
                search_fields=["field_comment"],
                output_fields=["id", "table_name", "field_name", "field_comment"],
                top_k=top_k,
                rerank_way="rerank",
                rerank_columns=["field_comment"],
                rrf_k=rrf_k,
            )
            candidates = []
            for chunk in chunks:
                field_name = str(chunk["source"].get("field_name") or "")
                if "--" in field_name or "//" in field_name:
                    continue
                if chunk["score"] > 0:
                    candidates.append({
                        "source": "数据库中的列",
                        "key": field_name,
                        "value": chunk["source"]["field_comment"],
                        "table_name": chunk["source"]["table_name"],
                        "column_name": field_name,
                        "score": chunk["score"],
                    })
            return candidates
        except Exception as exc:
            logger.warning("字段名索引搜索失败: %s", exc)
            return []

    async def _rank_candidates(
        self, question: str, entity: str, candidates: list[dict]
    ) -> dict[str, Any]:
        """LLM 排序 top5 + 困惑度；失败降级为候选原序前 5 + ambiguity=1。"""
        total_tokens = 0
        try:
            candidate_dict = {
                f"候选值{i+1}": (
                    f"{cand['value']}, 候选值来源：{cand['source']}, "
                    f"候选值所在表的表名: {cand['table_name']}, 候选值所处字段: {cand['column_name']}"
                )
                for i, cand in enumerate(candidates)
            }
            prompt = PromptBuilder.build_entity_ranking_prompt(question, entity, candidate_dict)
            total_tokens += count_tokens(prompt)
            system_prompt = "你是一个语义分析专家，擅长为实体挑选合适的候选值匹配项"
            total_tokens += count_tokens(system_prompt)

            response = await self.llm_client.chat_completion(
                user_prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )
            total_tokens += count_tokens(response)

            result = _parse_result_json(response)
            return {
                "ranked_candidates": [
                    candidates[int(str(ele).replace("候选值", "")) - 1]
                    for ele in result["top_candidates"]
                ],
                "token_num": total_tokens,
                "ambiguity_level": result["ambiguity_level"],
            }
        except Exception as exc:
            logger.warning("候选值排序失败: %s", exc)
            return {
                "ranked_candidates": candidates[:5],
                "token_num": total_tokens,
                "ambiguity_level": 1,
            }


class InformationRetrievalService:
    """相关信息召回服务（问答对 / 指标 / 术语三路并行）"""

    async def retrieve_all(
        self,
        question: str,
        dataset_id: str,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> dict[str, list]:
        try:
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=4) as executor:
                qa_future = loop.run_in_executor(
                    executor, self._retrieve_qa_pairs, question, dataset_id, top_k, rrf_k
                )
                index_future = loop.run_in_executor(
                    executor, self._retrieve_metrics, question, dataset_id, top_k, rrf_k
                )
                term_future = loop.run_in_executor(
                    executor, self._retrieve_terminology, question, dataset_id, top_k, rrf_k
                )
                qa_pair, index, terminology = await asyncio.gather(
                    qa_future, index_future, term_future
                )
            return {"qa_pair": qa_pair, "index": index, "terminology": terminology}
        except Exception as exc:
            logger.warning("信息召回失败: %s", exc, exc_info=True)
            return {"qa_pair": [], "index": [], "terminology": []}

    def _retrieve_qa_pairs(
        self, query: str, dataset_id: str, top_k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """检索问答对（few-shot 范例）。"""
        try:
            chunks = hybrid_search(
                query=query,
                collection_name=INDEX_NAMES["QA_PAIR"],
                es_index_name=INDEX_NAMES["QA_PAIR"],
                expr_columns={"dataset_id": [dataset_id]},
                search_fields=["question"],
                output_fields=["id", "question", "question_sql"],
                top_k=top_k,
                rerank_way="RRF",
                rrf_k=rrf_k,
            )
            return [chunk["source"] for chunk in chunks if chunk["score"] > 0]
        except Exception as exc:
            logger.warning("问答对检索失败: %s", exc)
            return []

    def _retrieve_metrics(
        self, query: str, dataset_id: str, top_k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """检索指标口径。"""
        try:
            chunks = hybrid_search(
                query=query,
                collection_name=INDEX_NAMES["INDEX"],
                es_index_name=INDEX_NAMES["INDEX"],
                expr_columns={"dataset_id": [dataset_id]},
                search_fields=["index_display_name"],
                output_fields=["id", "index_name", "index_display_name", "calculate_method"],
                top_k=top_k,
                rerank_way="RRF",
                rrf_k=rrf_k,
            )
            return [chunk["source"] for chunk in chunks if chunk["score"]]
        except Exception as exc:
            logger.warning("指标检索失败: %s", exc)
            return []

    def _retrieve_terminology(
        self, query: str, dataset_id: str, top_k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """检索术语（召回 top_k*5 再截 top_k）。"""
        try:
            chunks = hybrid_search(
                query=query,
                collection_name=INDEX_NAMES["PROFESSIONAL_TERMINOLOGY"],
                es_index_name=INDEX_NAMES["PROFESSIONAL_TERMINOLOGY"],
                expr_columns={"dataset_id": [dataset_id]},
                search_fields=["terminology", "synonyms"],
                output_fields=["id", "terminology", "synonyms", "terminology_explain"],
                top_k=top_k * 5,
                rerank_way="RRF",
                rrf_k=rrf_k,
            )
            return [chunk["source"] for chunk in chunks[:top_k] if chunk["score"] > 0]
        except Exception as exc:
            logger.warning("术语检索失败: %s", exc)
            return []


class SchemaSelectionService:
    """Schema 选择服务：单数据集全量启用 DDL 直选；跨数据集 LLM 筛选 + 实体覆盖"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def select_schema(self, dataset_id: str) -> tuple[list[str], list[str]]:
        """单数据集：取全部启用 DDL（清洗注释行），不筛表（lone-ai 现行行为）。"""
        try:
            table2ddls = store.get_enabled_ddls(dataset_id)
            return list(table2ddls.keys()), list(table2ddls.values())
        except Exception as exc:
            logger.warning("Schema 选择失败: %s", exc, exc_info=True)
            return [], []

    async def select_schema_for_cross_dataset(
        self,
        question: str,
        confirmed_entities: dict[str, Any],
        terminologies: list[dict[str, Any]],
        dataset_id: str,
        required_tables: list[str],
    ) -> tuple[list[str], list[str]]:
        """跨数据集分支：LLM 初筛 + 实体覆盖 + 必选表兜底。"""
        try:
            table2ddls = store.get_enabled_ddls(dataset_id)
            if not table2ddls:
                logger.warning("[跨数据集Schema选择] dataset_id=%s 无可用 DDL", dataset_id)
                return [], []

            llm_result = await self._filter_tables_with_llm(question, table2ddls, terminologies)
            selected_tables = [
                table_name.strip()
                for table_name in llm_result
                if isinstance(table_name, str) and table_name.strip()
            ] if isinstance(llm_result, list) else []
            selected_tables = self._ensure_entity_coverage(selected_tables, confirmed_entities)

            deduped: list[str] = []
            for table_name in selected_tables:
                if table_name in table2ddls and table_name not in deduped:
                    deduped.append(table_name)
            for table_name in required_tables:
                if table_name and table_name in table2ddls and table_name not in deduped:
                    deduped.append(table_name)
            return deduped, [table2ddls[table] for table in deduped]
        except Exception as exc:
            logger.warning("跨数据集 Schema 选择失败: %s", exc, exc_info=True)
            return [], []

    async def _filter_tables_with_llm(
        self, question: str, table2ddls: dict[str, str], terminologies: list[dict[str, Any]]
    ) -> Any:
        """LLM 筛表；失败降级为全选。"""
        try:
            candidates = [
                {"table_name": table_name, "fields": str(table_info or "").splitlines()}
                for table_name, table_info in table2ddls.items()
            ]
            prompt = PromptBuilder.build_schema_selection_prompt(
                question=question, ddl_candidates=candidates, retrieval_info={}
            )
            if terminologies:
                terminology_text = "#### 用户查询相关的术语解释或规则：\n"
                for chunk in terminologies:
                    terminology_text += f"            -术语：{chunk['terminology']}, 该术语相关解释如下\n"
                    terminology_text += f"            {chunk['terminology_explain']}\n"
                    if chunk.get("synonyms", ""):
                        terminology_text += f"            {chunk['terminology']}等同于：{chunk['synonyms']}\n"
                prompt += f"\n{terminology_text}"

            response = await self.llm_client.chat_completion(
                user_prompt=prompt,
                system_prompt="你是一个数据库专家，请按照要求选择合适的表结构。",
                temperature=0.1,
            )
            return _parse_result_json(response)
        except Exception as exc:
            logger.warning("LLM 筛选表失败（降级全选）: %s", exc, exc_info=True)
            return list(table2ddls.keys())

    @staticmethod
    def _ensure_entity_coverage(
        selected_tables: list, confirmed_entities: dict[str, Any]
    ) -> list:
        """强制包含候选实体所在的表。"""
        for _, eles in confirmed_entities.items():
            if not isinstance(eles, list):
                continue
            for ele in eles:
                if not isinstance(ele, dict):
                    continue
                table_name = str(ele.get("table_name") or "").replace(" ", "")
                if table_name and table_name not in selected_tables:
                    selected_tables.append(table_name)
        return selected_tables


class TableAnalysisService:
    """多表分析服务：外键关系 → join 路径文本（已改为参数化查询，见 store）"""

    def analyze_tables(self, selected_tables: list[str], dataset_id: str) -> tuple[bool, str]:
        try:
            if len(selected_tables) <= 1:
                return False, ""
            paths = store.get_foreign_key_paths(dataset_id, selected_tables)
            if not paths:
                return False, ""
            return True, "\n".join(paths)
        except Exception as exc:
            logger.warning("多表分析失败: %s", exc, exc_info=True)
            return False, ""


class SQLGenerationService:
    """SQL 生成服务"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def generate_sql(
        self,
        phase_result: dict,
        question: str,
        table_ddl: list[str],
        join_path: str,
        steps: list[dict] | None,
        few_shots_information: list[dict],
        index_information: list[dict],
        dimension_information: dict,
        terminology_information: list[dict],
        db_type: str,
        exist_error_information: list[dict],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """生成 SQL；结果写回 phase_result（sql_content/explain_content/sql_gen_status/...）。"""
        total_tokens = 0
        try:
            prompt = PromptBuilder.build_sql_generation_prompt(
                question=question,
                table_ddl=table_ddl,
                join_path=join_path,
                steps=steps,
                few_shots_information=few_shots_information,
                terminology_infomation=terminology_information,
                index_information=index_information,
                dimension_information=dimension_information,
                db_type=db_type,
            )
            total_tokens += count_tokens(prompt)
            system_prompt = "你是一个数据库专家，请按照要求生成SQL语句。"
            total_tokens += count_tokens(system_prompt)
            total_tokens += sum(count_tokens(msg["content"]) for msg in exist_error_information)

            response = await self.llm_client.chat_completion(
                user_prompt=prompt,
                system_prompt=system_prompt,
                history_messages=exist_error_information,
                temperature=temperature,
            )
            total_tokens += count_tokens(response)

            if not re.findall(r"<result>(.*)</result>", response, re.DOTALL):
                raise Nl2sqlError("LLM响应中未找到<result>标签")
            result = _parse_result_json(response)

            phase_result["sql_content"] = result["sql"]
            phase_result["explain_content"] = result["explain"]
            phase_result["sql_gen_status"] = "success"
            phase_result["sql_gen_error_information"] = []
            phase_result["sql_gen_tokens"] = total_tokens
        except Exception as exc:
            logger.warning("SQL 生成失败: %s", exc, exc_info=True)
            phase_result["sql_content"] = ""
            phase_result["explain_content"] = ""
            phase_result["sql_gen_status"] = "error"
            phase_result["sql_gen_error_information"] = [
                {"role": "user", "content": f"SQL生成失败: {exc}，请重新生成"}
            ]
            phase_result["sql_gen_tokens"] = total_tokens
        return phase_result


class SecurityCheckService:
    """安全检查服务"""

    def __init__(self):
        self.validator = SQLValidator()

    def check_security(self, sql: str) -> tuple[bool, Any, list[str]]:
        try:
            return self.validator.check_security(sql)
        except Exception as exc:
            logger.warning("SQL 安全检查异常: %s", exc, exc_info=True)
            return False, f"安全检查异常: {exc}", []


class SQLExecutionService:
    """SQL 执行服务（业务库只读）"""

    def __init__(self, datasource: dict[str, Any]):
        self.datasource = datasource

    def execute_sql(self, phase_result: dict) -> dict[str, Any]:
        """执行 SQL；结果/错误写回 phase_result（exec_result/sql_exec_status/...）。"""
        sql = phase_result["sql_content"]
        try:
            data = business_db.exec_query(self.datasource, sql)

            for row in data:
                for key, value in row.items():
                    if isinstance(value, float):
                        row[key] = round(value, 4)

            data_json_str = json.dumps(data, ensure_ascii=False)
            is_truncated = False
            if len(data_json_str) > MAX_RESULT_JSON_CHARS:
                # SQL 尾部含 asc 时尾部才是 Top-N，取后 30 条；否则前 30 条
                if "asc" in sql.lower()[-10:]:
                    data = data[-TRUNCATED_ROWS:]
                else:
                    data = data[:TRUNCATED_ROWS]
                is_truncated = True

            phase_result["exec_result"] = data
            phase_result["exec_truncated"] = is_truncated
            phase_result["sql_exec_status"] = "success"
            phase_result["sql_exec_error_information"] = []
        except Exception as exc:
            logger.warning("SQL 执行失败: %s", exc)
            phase_result["exec_result"] = []
            phase_result["exec_truncated"] = False
            phase_result["sql_exec_status"] = "error"
            phase_result["sql_exec_error_information"] = [
                {"role": "assistant", "content": f"SQL查询语句为：{sql}"},
                {"role": "user", "content": f"SQL执行报错，错误信息为: {exc}，请重新调整输出SQL语句"},
            ]
        return phase_result


class SQLResultPostProcessService:
    """复合问题 SQL 执行结果后处理（依赖分析 / 字段拆分 / 全局数量字段清理）"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def analyze_dependency_structure(
        self, question: str, sql_content: str, exec_result: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """分析复合问题的依赖关系、字段归属和公共字段。"""
        compact_result = self._compact_exec_result_for_llm(exec_result)
        prompt = PromptBuilder.build_result_post_process_prompt(question, sql_content, compact_result)
        response = await self.llm_client.chat_completion(
            system_prompt="你是专业的数据结果后处理助手。你只输出<result>标签包裹的JSON，不输出解释文字。",
            user_prompt=prompt,
            temperature=0,
        )
        analysis = _parse_result_json(response)
        if not isinstance(analysis, dict):
            raise Nl2sqlError("结果后处理大模型输出不是JSON对象")
        analysis.setdefault("sub_questions", [])
        analysis.setdefault("has_dependency", False)
        analysis.setdefault("field_mapping", {})
        analysis.setdefault("common_fields", [])
        return analysis

    async def analyze_removable_quantity_fields(
        self, question: str, sql_content: str, exec_result: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """分析适合从多行明细中拆出或移除的全局数量字段；失败走保守兜底。"""
        compact_result = self._compact_exec_result_for_llm(exec_result)
        prompt = PromptBuilder.build_removable_quantity_field_prompt(question, sql_content, compact_result)
        try:
            response = await self.llm_client.chat_completion(
                system_prompt="你是专业的数据结果后处理助手。你只输出<result>标签包裹的JSON，不输出解释文字。",
                user_prompt=prompt,
                temperature=0,
            )
            analysis = _parse_result_json(response)
            if not isinstance(analysis, dict):
                raise Nl2sqlError("可移除数量字段分析大模型输出不是JSON对象")
            analysis.setdefault("removable_quantity_fields", [])
            analysis.setdefault("explicit_quantity_fields", [])
            return self._normalize_removable_quantity_analysis(analysis)
        except Exception as exc:
            logger.warning("可移除数量字段分析失败，使用保守规则降级: %s", exc)
            return self._fallback_removable_quantity_analysis(exec_result)

    def split_exec_result(
        self, exec_result: list[dict[str, Any]], analysis: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """按字段归属拆分 SQL 执行结果，并对每一部分做完全重复行压缩。"""
        rows = self._normalize_exec_rows(exec_result)
        sub_questions = analysis.get("sub_questions") if isinstance(analysis, dict) else []
        field_mapping = analysis.get("field_mapping") if isinstance(analysis, dict) else {}
        if not isinstance(sub_questions, list) or not isinstance(field_mapping, dict):
            return []
        has_dependency = bool(analysis.get("has_dependency")) if isinstance(analysis, dict) else False
        common_fields = [] if has_dependency else self._get_common_fields_from_analysis(analysis, rows)

        split_parts = []
        for sub_question in sub_questions:
            if not isinstance(sub_question, dict):
                continue
            sub_question_id = str(sub_question.get("id") or "").strip()
            if not sub_question_id:
                continue
            fields = field_mapping.get(sub_question_id, [])
            if not isinstance(fields, list):
                continue
            fields = [str(field).strip() for field in fields if str(field).strip()]
            fields = self._merge_fields(common_fields, fields)
            part_rows = [
                {field: row.get(field) for field in fields if field in row}
                for row in rows
            ]
            part_rows = [row for row in part_rows if row]
            part_rows = self._dedupe_if_all_rows_same(part_rows)
            split_parts.append(
                {
                    "sub_question_id": sub_question_id,
                    "business_domain": sub_question.get("business_domain", ""),
                    "question": sub_question.get("question", ""),
                    "depends_on": sub_question.get("depends_on", []),
                    "fields": fields,
                    "rows": part_rows,
                    "row_count": len(part_rows),
                }
            )
        return split_parts

    def split_dependency_removable_quantity_columns(
        self,
        exec_result: list[dict[str, Any]],
        question: str,
        analysis: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """依赖型复合问题中，将可移除且通过兜底校验的全局数量字段拆出来。"""
        rows = self._normalize_exec_rows(exec_result)
        if len(rows) <= 1:
            return []
        explicit_quantity_fields = self._filter_valid_removable_quantity_fields(
            rows, self._get_explicit_quantity_fields(analysis)
        )
        if not explicit_quantity_fields:
            return []

        columns: list[str] = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)

        split_parts = []
        first_row = rows[0]
        for column in explicit_quantity_fields:
            split_parts.append(
                {
                    "sub_question_id": f"removable_quantity_{len(split_parts) + 1}",
                    "business_domain": "全局数量字段",
                    "question": f"{question}（{column}）",
                    "depends_on": [],
                    "fields": [column],
                    "rows": [{column: first_row.get(column)}],
                    "row_count": 1,
                }
            )

        remaining_rows = [
            {column: value for column, value in row.items() if column not in explicit_quantity_fields}
            for row in rows
        ]
        remaining_rows = [row for row in remaining_rows if row]
        if remaining_rows:
            split_parts.append(
                {
                    "sub_question_id": "dependency_remaining_result",
                    "business_domain": "依赖明细结果",
                    "question": question,
                    "depends_on": [],
                    "fields": [c for c in columns if c not in explicit_quantity_fields],
                    "rows": remaining_rows,
                    "row_count": len(remaining_rows),
                }
            )
        return split_parts

    def cleanup_format_output_quantity_fields(
        self,
        format_output: dict[str, Any],
        quantity_analysis: Optional[dict[str, Any]],
        keep_fields: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """从单业务格式化结果中移除重复的全局数量字段。"""
        if not isinstance(format_output, dict):
            return format_output
        keep_field_set = {str(field).strip() for field in (keep_fields or []) if str(field).strip()}
        removable_fields = [
            field
            for field in self._get_removable_quantity_fields(quantity_analysis)
            if field not in keep_field_set
        ]
        if not removable_fields:
            return format_output

        data = format_output.get("data")
        if not isinstance(data, list) or len(data) <= 1:
            return format_output
        rows = [row for row in data if isinstance(row, dict)]
        if len(rows) <= 1:
            return format_output

        removable_fields = self._filter_valid_removable_quantity_fields(rows, removable_fields)
        if not removable_fields:
            return format_output

        cleaned_output = copy.deepcopy(format_output)
        cleaned_rows = [
            {key: value for key, value in row.items() if key not in removable_fields}
            for row in rows
        ]
        cleaned_output["data"] = cleaned_rows
        cleaned_output["removed_quantity_fields"] = removable_fields
        if (
            cleaned_output.get("dimensions") in removable_fields
            or cleaned_output.get("metrics") in removable_fields
        ):
            cleaned_output["figure_type"] = "text"
            cleaned_output["dimensions"] = ""
            cleaned_output["metrics"] = ""
            cleaned_output["data_figure"] = []
        return cleaned_output

    @staticmethod
    def choose_final_exec_result(
        exec_result: list[dict[str, Any]],
        analysis: dict[str, Any],
        split_parts: list[dict[str, Any]],
    ) -> Any:
        """依赖时使用原始结果；不依赖时使用拆分结果。"""
        if not split_parts:
            return exec_result
        has_dependency = bool(analysis.get("has_dependency")) if isinstance(analysis, dict) else False
        if has_dependency:
            return exec_result
        return split_parts

    @staticmethod
    def _normalize_exec_rows(exec_result: Any) -> list[dict[str, Any]]:
        """将执行结果规整为行字典列表。"""
        if isinstance(exec_result, list):
            return [row for row in exec_result if isinstance(row, dict)]
        if isinstance(exec_result, dict):
            for key in ("rows", "data", "result", "records"):
                value = exec_result.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _dedupe_if_all_rows_same(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """如果某一部分每一行完全一致，仅保留一条。"""
        if len(rows) <= 1:
            return rows
        serialized_rows = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
        if len(set(serialized_rows)) == 1:
            return [rows[0]]
        return rows

    @staticmethod
    def _merge_fields(*field_groups: list[str]) -> list[str]:
        """按顺序合并字段并去重。"""
        merged_fields = []
        for fields in field_groups:
            for field in fields:
                field = str(field or "").strip()
                if field and field not in merged_fields:
                    merged_fields.append(field)
        return merged_fields

    @staticmethod
    def _get_common_fields_from_analysis(
        analysis: dict[str, Any], rows: list[dict[str, Any]]
    ) -> list[str]:
        """读取公共条件字段，并校验字段确实存在于执行结果。"""
        common_fields = analysis.get("common_fields", []) if isinstance(analysis, dict) else []
        if not isinstance(common_fields, list):
            return []
        columns: list[str] = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)
        return [
            field
            for field in [str(field).strip() for field in common_fields]
            if field and field in columns
        ]

    @staticmethod
    def _get_removable_quantity_fields(analysis: Optional[dict[str, Any]]) -> list[str]:
        if not isinstance(analysis, dict):
            return []
        fields = analysis.get("removable_quantity_fields", [])
        if not isinstance(fields, list):
            return []
        return [str(field).strip() for field in fields if str(field).strip()]

    @staticmethod
    def _get_explicit_quantity_fields(analysis: Optional[dict[str, Any]]) -> list[str]:
        if not isinstance(analysis, dict):
            return []
        fields = analysis.get("explicit_quantity_fields", [])
        if not isinstance(fields, list):
            return []
        return [str(field).strip() for field in fields if str(field).strip()]

    @staticmethod
    def _normalize_removable_quantity_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
        removable_fields = analysis.get("removable_quantity_fields", [])
        if not isinstance(removable_fields, list):
            removable_fields = []
        explicit_fields = analysis.get("explicit_quantity_fields", [])
        if not isinstance(explicit_fields, list):
            explicit_fields = []
        removable_fields = [str(f).strip() for f in removable_fields if str(f).strip()]
        explicit_fields = [
            str(f).strip() for f in explicit_fields if str(f).strip() in removable_fields
        ]
        return {
            "removable_quantity_fields": removable_fields,
            "explicit_quantity_fields": explicit_fields,
        }

    def _fallback_removable_quantity_analysis(
        self, exec_result: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """可移除数量字段分析失败时的保守兜底：列名含"总/total"且多行重复非零整数。"""
        rows = self._normalize_exec_rows(exec_result)
        columns: list[str] = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)
        removable_quantity_fields = [
            column
            for column in columns
            if self._is_quantity_field(column, [])
            and self._can_remove_repeated_quantity_field(rows, column)
            and any(keyword in str(column).lower() for keyword in ["总", "total"])
        ]
        return {
            "removable_quantity_fields": removable_quantity_fields,
            "explicit_quantity_fields": [],
        }

    def _filter_valid_removable_quantity_fields(
        self, rows: list[dict[str, Any]], removable_fields: list[str]
    ) -> list[str]:
        """对大模型给出的可移除字段做兜底校验。"""
        if len(rows) <= 1:
            return []
        return [
            field
            for field in removable_fields
            if self._can_remove_repeated_quantity_field(rows, field)
        ]

    @staticmethod
    def _can_remove_repeated_quantity_field(rows: list[dict[str, Any]], field: str) -> bool:
        """仅允许移除多行中完全重复的非零整数数量字段，避免误删分布明细数量。"""
        if not field or len(rows) <= 1:
            return False
        values = []
        for row in rows:
            if field not in row:
                return False
            numeric_value = SQLResultPostProcessService._parse_nonzero_integer_decimal(row.get(field))
            if numeric_value is None:
                return False
            values.append(numeric_value)
        return bool(values) and all(value == values[0] for value in values)

    @staticmethod
    def _is_quantity_field(column: str, quantity_fields: list[str]) -> bool:
        """判断字段名是否属于数量字段（编码/编号/时间/比例等排除）。"""
        column_text = str(column or "").strip()
        if not column_text:
            return False
        if column_text in quantity_fields:
            return True
        lower_column = column_text.lower()
        quantity_keywords = ["数量", "总数", "存量", "次数", "个数", "处数", "累计", "合计", "total", "count", "num"]
        non_quantity_keywords = ["编码", "编号", "code", "id", "水位", "差值", "阈值", "时间", "日期", "率", "比例"]
        if any(keyword in lower_column for keyword in non_quantity_keywords):
            return False
        return any(keyword in lower_column for keyword in quantity_keywords)

    @staticmethod
    def _parse_nonzero_integer_decimal(value: Any) -> Optional[Decimal]:
        """解析非空、非零整数数字；解析失败返回 None。"""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        try:
            numeric_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if numeric_value == 0:
            return None
        if numeric_value != numeric_value.to_integral_value():
            return None
        return numeric_value

    def _compact_exec_result_for_llm(self, exec_result: Any, max_rows: int = 5) -> dict[str, Any]:
        """压缩执行结果，避免后处理提示词过长。"""
        rows = self._normalize_exec_rows(exec_result)
        columns: list[str] = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)
        return {
            "columns": columns,
            "sample_rows": rows[:max_rows],
            "row_count": len(rows),
        }
