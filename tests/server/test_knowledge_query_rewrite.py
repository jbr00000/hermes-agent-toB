"""精准模式第 1 步：多轮指代消解（查询改写）测试。"""
from __future__ import annotations

from server.deployment_config import KnowledgeAuxLlmConfig, KnowledgeDeploymentConfig
from server.knowledge import aux_llm as aux_llm_module
from server.knowledge import query_rewrite


def _config() -> KnowledgeDeploymentConfig:
    return KnowledgeDeploymentConfig(
        enabled=True,
        aux_llm=KnowledgeAuxLlmConfig(
            base_url="http://llm-gw.internal/v1", model="qwen-27B-FP8"
        ),
    )


class _FakeAuxLlm:
    def __init__(self, output: str, *, boom: bool = False):
        self._output = output
        self._boom = boom
        self.calls: list[dict] = []

    def chat(self, *, system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if self._boom:
            raise RuntimeError("aux llm down")
        return self._output


_HISTORY = [
    {"role": "user", "content": "报销流程是什么？"},
    {"role": "assistant", "content": "报销分三步【1】……"},
]


def test_rewrite_extracts_result_tag(monkeypatch) -> None:
    fake = _FakeAuxLlm("补充说明\n<result>财务制度的报销额度上限是多少？</result>\n")
    monkeypatch.setattr(query_rewrite, "get_aux_llm", lambda config: fake)

    rewritten = query_rewrite.rewrite_query_with_history(
        "额度上限是多少？", _HISTORY, config=_config()
    )

    assert rewritten == "财务制度的报销额度上限是多少？"
    # prompt 里带上了历史与当前问题
    assert "报销流程是什么？" in fake.calls[0]["user"]
    assert "额度上限是多少？" in fake.calls[0]["user"]


def test_rewrite_without_history_returns_original(monkeypatch) -> None:
    fake = _FakeAuxLlm("<result>x</result>")
    monkeypatch.setattr(query_rewrite, "get_aux_llm", lambda config: fake)

    assert query_rewrite.rewrite_query_with_history("报销？", [], config=_config()) == "报销？"
    assert fake.calls == []  # 无历史不调用辅助模型


def test_rewrite_failure_falls_back_to_original(monkeypatch) -> None:
    fake = _FakeAuxLlm("", boom=True)
    monkeypatch.setattr(query_rewrite, "get_aux_llm", lambda config: fake)

    assert (
        query_rewrite.rewrite_query_with_history("那额度呢？", _HISTORY, config=_config())
        == "那额度呢？"
    )


def test_rewrite_identical_output_returns_original(monkeypatch) -> None:
    fake = _FakeAuxLlm("<result>那额度呢？</result>")
    monkeypatch.setattr(query_rewrite, "get_aux_llm", lambda config: fake)

    assert (
        query_rewrite.rewrite_query_with_history("那额度呢？", _HISTORY, config=_config())
        == "那额度呢？"
    )


def test_rewrite_without_result_tag_uses_raw_output(monkeypatch) -> None:
    fake = _FakeAuxLlm("  财务制度的报销额度上限是多少？  ")
    monkeypatch.setattr(query_rewrite, "get_aux_llm", lambda config: fake)

    rewritten = query_rewrite.rewrite_query_with_history(
        "额度上限是多少？", _HISTORY, config=_config()
    )
    assert rewritten == "财务制度的报销额度上限是多少？"


def test_aux_llm_configured_requires_full_config() -> None:
    assert aux_llm_module.aux_llm_configured(_config()) is True
    assert (
        aux_llm_module.aux_llm_configured(KnowledgeDeploymentConfig(enabled=True)) is False
    )
    assert aux_llm_module.aux_llm_configured(KnowledgeDeploymentConfig(enabled=False)) is False


def test_request_context_normalizes_search_mode() -> None:
    from server.knowledge import request_context

    assert request_context.normalize_search_mode("precise") == "precise"
    assert request_context.normalize_search_mode("FAST") == "fast"
    assert request_context.normalize_search_mode("weird") == "fast"
    assert request_context.normalize_search_mode(None) == "fast"
    # 默认 fast；set/get 闭环
    assert request_context.get_search_mode() == "fast"
    request_context.set_search_mode("precise")
    assert request_context.get_search_mode() == "precise"
    request_context.set_search_mode("fast")
