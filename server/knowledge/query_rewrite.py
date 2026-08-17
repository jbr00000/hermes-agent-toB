"""多轮指代消解：把依赖上下文的追问改写成独立检索问题（精准模式第 1 步）。

Prompt 移植自 lone-ai ``core/prompt_bank/prompt_rag.py`` 的
``rewrite_current_query_prompt``（零依赖纯字符串，覆盖代词消解/省略补充/
话题延续/不改写四个场景）。改写失败或无历史时原样返回——这是降级路径，
不是错误。

用法：knowledge 精准模式下，agent 构建前用会话历史改写当前用户消息，
改写结果通过 prompt 钉给主模型作为 knowledge_search 的 query。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from server.deployment_config import KnowledgeDeploymentConfig

from .aux_llm import get_aux_llm

logger = logging.getLogger(__name__)

_MAX_HISTORY_TURNS = 6  # 只带最近几轮，约束辅助模型输入长度
_RESULT_PATTERN = re.compile(r"<result>(.*?)</result>", re.DOTALL)

# 移植自 lone-ai prompt_rag.rewrite_current_query_prompt（{input_data} 为
# 「历史对话 + 当前问题」的拼接）
REWRITE_QUERY_PROMPT = """
你是一位专业的查询改写专家，负责在多轮对话中改写用户当前问题。

## 背景说明
你将获得：
1. 当前用户问题
2. 历史对话记录（包含用户问题和助手回答）

## 改写场景与规则

### 场景1：代词消解
将问题中的代词替换为具体实体名称。
- 示例1：
  历史：用户问"iPhone 15有哪些颜色？"，助手回答后
  当前："它的价格是多少？"
  改写："iPhone 15的价格是多少？"
- 需处理的代词：它、他、她、这个、那个、这些、那些、其、该

### 场景2：省略补充
补充承上省略的主语、宾语等核心成分。
- 示例：
  历史：用户问"MacBook Pro的性能如何"
  当前："配置怎么样？"
  改写："MacBook Pro的配置怎么样？"

### 场景3：话题延续追问
处理带有追问语气的问题，保持话题连贯性。
- 示例：
  历史：讨论"Python编程语言"
  当前："那它的应用领域有哪些？"
  改写："Python编程语言的应用领域有哪些？"

### 场景4：不改写的情况
以下情况直接返回原问题，不做改写：
- 问题本身已经完整明确，不依赖历史对话
- 开启全新话题（与历史对话无语义关联）
- 问题长度超过50字（避免过度复杂化）

## 改写标准
1. **意图一致性**：改写后与原问题查询意图完全一致，不增加、不改变意图
2. **独立性**：脱离历史对话后，改写后的问题仍能清晰表达完整的查询意图
3. **简洁性**：避免冗余表述，保持问题简洁自然
4. **准确性**：确保替换的实体名称与历史对话中提及的一致

## 输出格式
<result>
改写后的问题
</result>

## 注意事项
- 如果问题本身完整且不依赖历史，直接返回原问题
- 只输出改写后的问题，不要任何解释或额外内容
- 改写后的问题应该是自然的中文表达

## 用户输入
{input_data}
"""


def _format_input(query: str, history: list[dict[str, Any]]) -> str:
    lines = ["【历史对话】"]
    for message in history[-_MAX_HISTORY_TURNS:]:
        role = "用户" if message.get("role") == "user" else "助手"
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append("")
    lines.append("【当前问题】")
    lines.append(query)
    return "\n".join(lines)


def rewrite_query_with_history(
    query: str,
    history: list[dict[str, Any]],
    *,
    config: KnowledgeDeploymentConfig,
) -> str:
    """Rewrite a follow-up into a standalone query; original on any degradation.

    history 为 OpenAI 格式的历史消息（不含本轮问题）。无历史、辅助模型
    调用失败、返回为空时都原样返回 ``query``。
    """
    text = str(query or "").strip()
    if not text or not history:
        return text
    try:
        output = get_aux_llm(config).chat(
            system_prompt="你是一位专业的查询改写专家。",
            user_prompt=REWRITE_QUERY_PROMPT.replace(
                "{input_data}", _format_input(text, history)
            ),
            temperature=0.3,
        )
    except Exception as exc:
        logger.warning("knowledge query rewrite failed, use original: %s", exc)
        return text
    match = _RESULT_PATTERN.search(output)
    rewritten = (match.group(1) if match else output).strip()
    if not rewritten or rewritten == text:
        return text
    logger.info("knowledge query rewritten: %r -> %r", text[:50], rewritten[:50])
    return rewritten
