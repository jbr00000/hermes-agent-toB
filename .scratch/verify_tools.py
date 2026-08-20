"""Scratch: verify which tools the model would actually see per mode/permission.

Mimics server boot: HERMES_HOME + .env loaded, headless, discovery triggered.
Run with conda python; prints ONLY tool names (no secrets).
"""
import os
import sys

HERMES_HOME = r"F:\project\Hermes Agent\hermes-agent\.hermes-dev"
os.environ["HERMES_HOME"] = HERMES_HOME
sys.path.insert(0, r"F:\project\Hermes Agent\hermes-agent")

from dotenv import load_dotenv

load_dotenv(os.path.join(HERMES_HOME, ".env"))
os.environ.setdefault("HERMES_HEADLESS", "1")

import model_tools  # noqa: F401  (triggers builtin tool discovery)
from toolsets import resolve_multiple_toolsets
from tools.registry import registry
from server.tool_policy import resolve_toolsets

for mode, perm in [("chat", "read"), ("plan", "read"), ("execute", "read"),
                   ("execute", "controlled"), ("execute", "full")]:
    toolsets = resolve_toolsets(mode=mode, features={}, permission_mode=perm)
    names = resolve_multiple_toolsets(toolsets)
    defs = registry.get_definitions(set(names), quiet=True)
    visible = sorted(d.get("name") or d.get("function", {}).get("name") for d in defs)
    print(f"[{mode}/{perm}] toolsets={toolsets}")
    print(f"  visible to model ({len(visible)}): {visible}")
