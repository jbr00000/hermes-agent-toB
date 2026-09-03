"""NL2SQL（问数）算法包 —— 移植自 lone-ai ``code/algorithm`` 的最小闭包。

只搬问数主链路：实体抽取 → 候选匹配 → 知识召回 → 选表 → SQL 生成/校验/执行
→ 结果后处理。澄清（Clarification）、问题分解（QueryDecomposition）、记忆写回、
Redis 阶段缓存，以及水项目（地铁渗漏监测）的业务硬编码一律剔除。

复用本仓基建：
  - LLM：server.runtime_config + hermes_cli.runtime_provider（5 个受支持 provider，
    仅 chat_completions api_mode；kimi-coding 的 anthropic_messages 不支持）
  - embedding/ES/Milvus/rerank：server.knowledge 的四个客户端（deployment.yaml
    ``knowledge:`` 段，向量维度 1024）
  - 元数据读取：server.nl2sql.store 的算法端助手（Boolean enabled、参数化查询）

阶段3 先整体可用；阶段6 再按阶段拆模块。
"""
from __future__ import annotations


class Nl2sqlError(Exception):
    """问数链路的统一异常（路由层捕获后转成 SSE error 事件）。"""
