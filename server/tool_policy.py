"""Toolset policy for the headless server."""
from __future__ import annotations

from collections.abc import Mapping


def resolve_toolsets(
    *,
    mode: str | None,
    features: Mapping[str, object] | None,
    permission_mode: str = "read",
    knowledge: bool = True,
) -> list[str]:
    """Return the AIAgent toolsets allowed for this request mode.

    Plan mode is enforced at the toolset layer: it may inspect customer data
    through read-only DB tooling, but it cannot use terminal or desktop tools.

    The "web" toolset (web_search / web_extract) is read-only retrieval and is
    allowed in every mode; when no web backend is configured (SEARXNG_URL /
    FIRECRAWL_API_URL unset), check_fn hides the tools so the model never sees
    them — adding "web" here is a no-op in that case.

    The "browser" toolset (P3 兜底, docs/联网检索接入方案.md §8) is
    write-capable (click/type produce side effects in customer systems), so
    it requires BOTH the ``browser_automation`` feature flag AND full
    permission mode; plan/chat never see it. When no browser backend is
    reachable (no BROWSER_CDP_URL / browser.cdp_url and no local
    agent-browser), check_fn hides the tools — adding "browser" here is a
    no-op in that case.

    "controlled" 受控写入：agent 拿到 terminal 工具集，但每条 terminal/process
    命令在执行前都要经 server.tool_gate 的 pre_tool_call 钩子等待用户在
    Web 上批准（运行中途 human-in-the-loop）。browser 仍属 full 专属。

    "file" 工具集（read_file / write_file / patch / search_files）挂在
    controlled 与 full：沙箱后端是 Docker 时，file 工具通过 cwd override
    在容器内的任务工作区（/workspace/tasks/<task_id>/）读写，产物随后经
    artifacts 接口供用户下载。plan/chat/knowledge 保持只读，不挂 file。

    "knowledge" 工具集（knowledge_search）是只读检索，chat/plan/execute 全
    部放行——未配置知识库后端时 check_fn 会隐藏工具，加在这里是 no-op。
    mode="knowledge"（知识库问答）是专用模式：只挂 knowledge 工具集，由
    agent_factory 的 RAG prompt 约束"先检索、再作答、标引用"。

    ``knowledge=False`` 是运行级开关（agent 计划/执行时用户可选"不带知识
    库"）：把 knowledge 工具集从结果中摘除，模型本轮完全看不到
    knowledge_search。专用 mode="knowledge" 不受此开关影响。
    """
    normalized_mode = (mode or "execute").strip().lower()
    if normalized_mode == "knowledge":
        return ["knowledge"]
    if normalized_mode in {"chat", "plan"}:
        toolsets = ["db", "web", "knowledge"]
    elif permission_mode == "full":
        toolsets = ["db", "terminal", "file", "web", "knowledge"]
        if (features or {}).get("browser_automation"):
            toolsets.append("browser")
    elif permission_mode == "controlled":
        toolsets = ["db", "terminal", "file", "web", "knowledge"]
    else:
        toolsets = ["db", "web", "knowledge"]
    if not knowledge:
        toolsets = [name for name in toolsets if name != "knowledge"]
    return toolsets
