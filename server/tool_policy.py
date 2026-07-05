"""Toolset policy for the headless server."""
from __future__ import annotations

from collections.abc import Mapping


def resolve_toolsets(*, mode: str | None, features: Mapping[str, object] | None) -> list[str]:
    """Return the AIAgent toolsets allowed for this request mode.

    Plan mode is enforced at the toolset layer: it may inspect customer data
    through read-only DB tooling, but it cannot use terminal or desktop tools.
    """
    normalized_mode = (mode or "execute").strip().lower()
    if normalized_mode == "plan":
        return ["db", "session_search"]

    enabled = ["db", "session_search", "terminal"]
    feature_flags = features or {}
    if bool(feature_flags.get("computer_use")):
        enabled.append("computer_use")
    return enabled
