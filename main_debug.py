"""VSCode-friendly debug harness for the Hermes agent runtime.

This file is intentionally a script, not a CLI wrapper. Open it in VSCode,
pick the Hermes Python environment, set breakpoints in the functions below,
and press "Run and Debug".

Good first breakpoints:
    1. resolve_debug_runtime()
    2. build_debug_agent()
    3. run_debug_conversation()
    4. verify_output_file()
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Edit these constants directly while debugging in VSCode.
DEBUG_XLSX_PATH = Path(
    r"F:\工作文档\lemashi\轨道公司\智能管控平台\智库平台-软件开发费用测算V0.2.xlsx"
)
DEBUG_OUTPUT_PATH = REPO_ROOT / "test_outputs" / "智库平台-软件开发费用测算V0.2_debug摘要.txt"
DEBUG_PROVIDER = "deepseek"
DEBUG_MODEL = "deepseek-v4-pro"

# None means "all built-in toolsets" at the AIAgent/model_tools layer.
# Set to ["terminal", "file", "code_execution"] etc. when you want a smaller surface.
DEBUG_TOOLSETS: list[str] | None = None

DEBUG_PLATFORM = "cli"
DEBUG_MAX_ITERATIONS = 90
DEBUG_SKIP_CONTEXT_FILES = False
DEBUG_SKIP_MEMORY = False

DEBUG_HERMES_HOME = REPO_ROOT / ".hermes-dev"
DEBUG_RUN_ROOT = REPO_ROOT / "test_outputs" / "debug_runs"


SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "secret",
    "password",
    "bearer",
    "credential",
)


def setup_debug_environment() -> None:
    """Set the process state that the non-interactive agent path expects."""
    os.chdir(REPO_ROOT)
    os.environ["HERMES_HOME"] = str(DEBUG_HERMES_HOME)
    os.environ["HERMES_YOLO_MODE"] = "1"
    os.environ["HERMES_ACCEPT_HOOKS"] = "1"
    os.environ["HERMES_FEATURE_HOST_TERMINAL"] = "1"

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_debug_config() -> dict[str, Any]:
    """Load the same user/project config that Hermes uses in normal runs."""
    from hermes_cli.config import load_config

    config = load_config()
    return config


def resolve_debug_reasoning_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the agent reasoning setting in a way that is easy to inspect."""
    from hermes_constants import parse_reasoning_effort

    agent_config = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    parsed = parse_reasoning_effort(agent_config.get("reasoning_effort"))
    if parsed is not None:
        return parsed

    # DeepSeek V4 thinking mode can complicate tool-call replay. Keep debug
    # runs conservative unless config.yaml explicitly says otherwise.
    return {"enabled": False}


def resolve_debug_runtime(provider: str, model: str) -> dict[str, Any]:
    """Resolve base_url, api_key, api_mode, provider, and credential pool."""
    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(
        requested=provider,
        target_model=model,
    )
    return runtime


def create_debug_session_db():
    """Create the same optional SQLite session DB used by oneshot mode."""
    try:
        from hermes_state import SessionDB

        return SessionDB()
    except Exception as exc:
        print(f"[debug] SessionDB unavailable: {exc}")
        return None


def debug_clarify_callback(question: str, choices=None) -> str:
    """Make non-interactive clarification requests deterministic."""
    if choices:
        return (
            "[debug mode: no user input is available. Pick the best option from "
            f"{choices} and continue.]"
        )
    return "[debug mode: make the most reasonable assumption and continue.]"


def compact_value(value: Any, *, limit: int = 1200) -> Any:
    """Keep debug traces readable and JSON serializable."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): compact_value(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [compact_value(item, limit=limit) for item in value[:20]]
    return str(value)


def make_debug_callbacks(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Return callbacks that expose major runtime events while debugging."""

    def record(event_type: str, **payload: Any) -> None:
        item = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event_type,
            **compact_value(payload),
        }
        trace.append(item)
        print(f"[debug:{event_type}] {compact_value(payload, limit=260)}")

    def on_tool_start(tool_call_id: str, name: str, display_args: Any) -> None:
        record("tool_start", tool_call_id=tool_call_id, name=name, args=display_args)

    def on_tool_complete(
        tool_call_id: str,
        name: str,
        display_args: Any,
        function_result: Any,
    ) -> None:
        record(
            "tool_complete",
            tool_call_id=tool_call_id,
            name=name,
            args=display_args,
            result=function_result,
        )

    def on_status(kind: str, message: str) -> None:
        record("status", kind=kind, message=message)

    def on_notice(notice: Any) -> None:
        record("notice", notice=notice)

    def on_event(event_type: str, payload: dict[str, Any]) -> None:
        record("event", event_type=event_type, payload=payload)

    def on_step(api_call_count: int, previous_tools: Any) -> None:
        record("step", api_call_count=api_call_count, previous_tools=previous_tools)

    return {
        "tool_start_callback": on_tool_start,
        "tool_complete_callback": on_tool_complete,
        "status_callback": on_status,
        "notice_callback": on_notice,
        "event_callback": on_event,
        "step_callback": on_step,
    }


def build_debug_agent(
    runtime: dict[str, Any],
    config: dict[str, Any],
    trace: list[dict[str, Any]],
):
    """Construct AIAgent directly so VSCode can step into framework code."""
    from hermes_cli.fallback_config import get_fallback_chain
    from run_agent import AIAgent

    callbacks = make_debug_callbacks(trace)
    session_db = create_debug_session_db()
    fallback_chain = get_fallback_chain(config)
    reasoning_config = resolve_debug_reasoning_config(config)

    # Put a breakpoint on the next line to inspect every constructor argument.
    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        model=DEBUG_MODEL,
        max_iterations=DEBUG_MAX_ITERATIONS,
        enabled_toolsets=DEBUG_TOOLSETS,
        # Keep Hermes' own startup banner quiet because it includes masked
        # credential hints. The debug callbacks below still expose tool flow.
        quiet_mode=True,
        platform=DEBUG_PLATFORM,
        session_db=session_db,
        credential_pool=runtime.get("credential_pool"),
        fallback_model=fallback_chain or None,
        reasoning_config=reasoning_config,
        clarify_callback=debug_clarify_callback,
        skip_context_files=DEBUG_SKIP_CONTEXT_FILES,
        skip_memory=DEBUG_SKIP_MEMORY,
        **callbacks,
    )
    return agent


def build_sample_prompt(xlsx_path: Path, output_path: Path) -> str:
    """Build the exact task prompt used to exercise the agent."""
    return f"""请帮我读取“{xlsx_path}”这个文档，然后总结这个文档中的主要内容并写入一个txt文档保存到本地。

要求：
1. 请把txt保存到“{output_path}”。
2. 你可以使用终端、Python、文件读写、Excel解析等工具；已授权主机终端权限。
3. 完成后请回复保存路径和摘要要点。
"""


def run_debug_conversation(agent, prompt: str) -> dict[str, Any]:
    """Run one full agent turn. Step into this call for the main runtime."""
    # Put a breakpoint on the next line, then Step Into to inspect the loop.
    result = agent.run_conversation(prompt)
    return result


def verify_output_file(output_path: Path) -> dict[str, Any]:
    """Check whether the agent produced the expected text file."""
    if not output_path.exists():
        return {
            "exists": False,
            "path": str(output_path),
        }

    text = output_path.read_text(encoding="utf-8", errors="replace")
    return {
        "exists": True,
        "path": str(output_path),
        "bytes": output_path.stat().st_size,
        "line_count": text.count("\n") + 1,
        "preview": "\n".join(text.splitlines()[:30]),
    }


def redact_secrets(value: Any) -> Any:
    """Remove obvious secrets before writing debug JSON to disk."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


def write_debug_artifacts(
    prompt: str,
    result: dict[str, Any],
    runtime: dict[str, Any],
    verification: dict[str, Any],
    trace: list[dict[str, Any]],
) -> Path:
    """Persist inspectable artifacts for a debug run."""
    run_dir = DEBUG_RUN_ROOT / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    final_response = result.get("final_response") or ""
    (run_dir / "debug_prompt.txt").write_text(prompt, encoding="utf-8")
    (run_dir / "debug_final_response.txt").write_text(final_response, encoding="utf-8")

    payload = {
        "provider": runtime.get("provider"),
        "model": DEBUG_MODEL,
        "base_url": runtime.get("base_url"),
        "api_mode": runtime.get("api_mode"),
        "result": result,
        "verification": verification,
        "trace": trace,
    }
    safe_payload = redact_secrets(payload)
    (run_dir / "debug_result.json").write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return run_dir


def main() -> dict[str, Any]:
    setup_debug_environment()
    DEBUG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    trace: list[dict[str, Any]] = []

    config = load_debug_config()
    runtime = resolve_debug_runtime(DEBUG_PROVIDER, DEBUG_MODEL)
    agent = build_debug_agent(runtime, config, trace)

    prompt = build_sample_prompt(DEBUG_XLSX_PATH, DEBUG_OUTPUT_PATH)
    result = run_debug_conversation(agent, prompt)
    verification = verify_output_file(DEBUG_OUTPUT_PATH)
    run_dir = write_debug_artifacts(prompt, result, runtime, verification, trace)

    print("\n[debug] final response:")
    print(result.get("final_response") or "")
    print(f"\n[debug] output exists: {verification.get('exists')}")
    print(f"[debug] output file: {DEBUG_OUTPUT_PATH}")
    print(f"[debug] artifacts: {run_dir}")

    return {
        "result": result,
        "verification": verification,
        "artifacts": str(run_dir),
        "trace": trace,
    }


if __name__ == "__main__":
    DEBUG_RESULT = main()
