"""Toolset policy for the headless server."""
from __future__ import annotations

from collections.abc import Mapping


def resolve_toolsets(
    *,
    mode: str | None,
    features: Mapping[str, object] | None,
    permission_mode: str = "read",
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
    """
    normalized_mode = (mode or "execute").strip().lower()
    if normalized_mode in {"chat", "plan"}:
        return ["db", "web"]
    if permission_mode == "full":
        toolsets = ["db", "terminal", "web"]
        if (features or {}).get("browser_automation"):
            toolsets.append("browser")
        return toolsets
    return ["db", "web"]
