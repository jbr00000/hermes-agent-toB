"""Per-request knowledge-search context.

``/chat`` 的 run_agent 线程在调 agent 前写入本轮的检索模式（fast/precise），
工具层（tools/knowledge_search.py，同线程同步派发）读取它并写进工具结果。
P2 的检索编排器（查询扩展/降级重试）也挂在这个上下文上——模式由请求决定，
不经模型透传，避免"模型忘了传参"的不确定性。
"""
from __future__ import annotations

from contextvars import ContextVar

_SEARCH_MODES = ("fast", "precise")

_search_mode: ContextVar[str] = ContextVar("knowledge_search_mode", default="fast")


def normalize_search_mode(value: object) -> str:
    mode = str(value or "fast").strip().lower()
    return mode if mode in _SEARCH_MODES else "fast"


def set_search_mode(mode: str) -> None:
    _search_mode.set(normalize_search_mode(mode))


def get_search_mode() -> str:
    return _search_mode.get()
