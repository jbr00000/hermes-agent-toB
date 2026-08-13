"""Enterprise knowledge-base construction pipeline.

上传 → 解析（MinerU / 本地）→ 分块 → embedding → 双写 ES（全文）与 Milvus（向量）。
MySQL（knowledge_chunks 表）是事实源，ES/Milvus 是可重建的检索投影。
本期不含 RAG 检索问答；配置入口是 deployment.yaml 的 ``knowledge:`` 段
（``server/deployment_config.py`` 的 ``KnowledgeDeploymentConfig``）。
"""
from __future__ import annotations


class KnowledgeError(RuntimeError):
    """Base error for the knowledge pipeline (parse / embed / sync)."""


class KnowledgeDisabledError(KnowledgeError):
    """knowledge.enabled=false（或配置缺失）时抛出；路由层映射为 404。"""
