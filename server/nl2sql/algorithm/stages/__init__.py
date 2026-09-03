"""问数算法的三个执行阶段（lone-ai nl2sql_api.py 的模块化拆分）。

阶段映射（对应前端三段式折叠卡片）：
  understand = lone-ai phase1-4（保持原问题 → 实体抽取 → 候选匹配 → 不澄清整理）
  generate   = lone-ai phase6-8（三路召回 → 选表/表分析 → SQL生成+安检+执行，4 次重试）
  result     = lone-ai phase9 + 格式化（跨数据集结果后处理 → 维度值映射 → LLM 格式化）

每个模块提供 run_single / run_cross 两个入口（单数据集 / 跨数据集流），
编排器（orchestrator.py）只做数据集解析、流程编排与统一返回组装。
"""
