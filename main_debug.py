"""VSCode 调试脚手架：用来在 VSCode 里单步调试 Hermes agent 运行时。

【这个文件是干嘛的】
一个**专门给调试用的脚本**（不是产品 CLI，也不走 server/）。它跑一个固定的真实
任务——「读一个 Excel → 总结内容 → 写成 txt 存本地」——把 agent 完整跑一遍，
方便你在 VSCode 里打断点、单步进入框架代码，看 agent 内部到底怎么转的。

【整体流程】main() 一路调下来：
    setup_debug_environment()    ① 设环境变量/工作目录（HERMES_HOME、host_terminal 等）
    load_debug_config()          ② 读 config.yaml（用户/项目配置）
    resolve_debug_runtime()      ③ 解析 provider/model 的 base_url / api_key / api_mode / 凭据池
    build_debug_agent()          ④ 直接 new 一个 AIAgent，挂上一组调试回调
    build_sample_prompt()        ⑤ 拼出「读 Excel → 写摘要 txt」的任务提示词
    run_debug_conversation()     ⑥ 跑一轮 agent（★ 主入口，断点打这里再 Step Into）
    verify_output_file()         ⑦ 检查产物 txt 有没有真的生成
    write_debug_artifacts()      ⑧ 把 prompt / 最终回答 / trace 落盘到 debug_runs/<时间戳>/

【产物】每次跑会在 test_outputs/debug_runs/<时间戳>/ 下生成：
    debug_prompt.txt            本次任务提示词
    debug_final_response.txt    agent 最终回答
    debug_result.json           完整 result + verification + trace（已脱敏）

【安全】debug_result.json 写盘前过 redact_secrets，把 api_key/authorization 之类
敏感字段替换成 [REDACTED]；trace 里大字符串也会被 compact_value 截断。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# 把仓库根目录塞进 sys.path，保证「从根目录直接跑本文件」时 import 能找到 hermes_* 模块。
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEBUG_XLSX_PATH = Path(
    r"F:\工作文档\lemashi\轨道公司\智能管控平台\智库平台-软件开发费用测算V0.2.xlsx"
)
DEBUG_OUTPUT_PATH = REPO_ROOT / "test_outputs" / "智库平台-软件开发费用测算V0.2_debug摘要.txt"
DEBUG_PROVIDER = "deepseek"
DEBUG_MODEL = "deepseek-v4-pro"

# None = 用 AIAgent/model_tools 层的全部内置工具集；
# 想缩小攻击面就列出来，如 ["terminal", "file", "code_execution"]。
DEBUG_TOOLSETS: list[str] | None = None

DEBUG_PLATFORM = "cli"
DEBUG_MAX_ITERATIONS = 90
DEBUG_SKIP_CONTEXT_FILES = False
DEBUG_SKIP_MEMORY = False

DEBUG_HERMES_HOME = REPO_ROOT / ".hermes-dev"
DEBUG_RUN_ROOT = REPO_ROOT / "test_outputs" / "debug_runs"


# 脱敏用的「敏感字段名片段」——key 名（小写）里命中任一片段，值就替换成 [REDACTED]。
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


# ── ① 设进程环境：HERMES_HOME、host_terminal、UTF-8 stdio 等 ───────────
def setup_debug_environment() -> None:
    """Set the process state that the non-interactive agent path expects."""
    os.chdir(REPO_ROOT)
    os.environ["HERMES_HOME"] = str(DEBUG_HERMES_HOME)
    os.environ["HERMES_YOLO_MODE"] = "1"
    os.environ["HERMES_ACCEPT_HOOKS"] = "1"
    os.environ["HERMES_FEATURE_HOST_TERMINAL"] = "1"   # 开主机终端权限（任务里要用 shell）

    try:
        sys.stdout.reconfigure(encoding="utf-8")       # Windows 下避免中文乱码
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── ② 读 config.yaml（和正常 hermes 运行用的是同一份配置）──────────────
def load_debug_config() -> dict[str, Any]:
    """Load the same user/project config that Hermes uses in normal runs."""
    from hermes_cli.config import load_config

    config = load_config()
    return config


# ── reasoning（思考模式）配置：debug 时默认关，避免 DeepSeek V4 thinking 干扰工具回放 ─
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


# ── ③ 解析运行时：从 provider/model 拿到 base_url / api_key / api_mode / 凭据池 ──
def resolve_debug_runtime(provider: str, model: str) -> dict[str, Any]:
    """Resolve base_url, api_key, api_mode, provider, and credential pool."""
    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(
        requested=provider,
        target_model=model,
    )
    return runtime


# ── 可选：建一个 SessionDB（oneshot 模式用的同一个 SQLite 会话库）─────────
def create_debug_session_db():
    """Create the same optional SQLite session DB used by oneshot mode."""
    try:
        from hermes_state import SessionDB

        return SessionDB()
    except Exception as exc:
        print(f"[debug] SessionDB unavailable: {exc}")
        return None


# ── 澄清回调：调试时没有真人输入，让它返回一句固定的「自己挑合理的继续」──
def debug_clarify_callback(question: str, choices=None) -> str:
    """Make non-interactive clarification requests deterministic."""
    if choices:
        return (
            "[debug mode: no user input is available. Pick the best option from "
            f"{choices} and continue.]"
        )
    return "[debug mode: make the most reasonable assumption and continue.]"


# ── trace 截断工具：把任意值压成「可读 + 可 JSON」的长度（默认 1200 字符）──
def compact_value(value: Any, *, limit: int = 1200) -> Any:
    """Keep debug traces readable and JSON serializable."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): compact_value(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [compact_value(item, limit=limit) for item in value[:20]]   # 列表只留前 20 项
    return str(value)


# ── 一组调试回调：把 agent 运行时的事件（工具开始/完成、状态、步骤…）记进 trace 并打印 ──
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


# ── ④ 直接 new AIAgent（不走 CLI/server），挂上调试回调 + 凭据池 + fallback 链 ──
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

    # 想逐个检查构造参数，就在下一行打断点。
    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        model=DEBUG_MODEL,
        max_iterations=DEBUG_MAX_ITERATIONS,
        enabled_toolsets=DEBUG_TOOLSETS,
        # quiet_mode=True：压掉 Hermes 自家启动横幅（里面有打码的凭据提示）。
        # 下面的调试回调照样会把工具流转出来。
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


# ── ⑤ 拼「读 Excel → 写摘要 txt」的任务提示词（已授权主机终端权限）──────
def build_sample_prompt(xlsx_path: Path, output_path: Path) -> str:
    """Build the exact task prompt used to exercise the agent."""
    return f"""请帮我读取“{xlsx_path}”这个文档，然后总结这个文档中的主要内容并写入一个txt文档保存到本地。

要求：
1. 请把txt保存到“{output_path}”。
2. 你可以使用终端、Python、文件读写、Excel解析等工具；已授权主机终端权限。
3. 完成后请回复保存路径和摘要要点。
"""


# ── ⑥ 跑一轮 agent（★ 主入口）：想看主循环，在下一行打断点再 Step Into ──
def run_debug_conversation(agent, prompt: str) -> dict[str, Any]:
    """Run one full agent turn. Step into this call for the main runtime."""
    # 想进对话主循环，就在下一行打断点，然后 Step Into。
    result = agent.run_conversation(prompt)
    return result


# ── ⑦ 校验产物：那个 txt 到底生成没、多大、前 30 行预览 ────────────────
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


# ── 脱敏：写盘前把 api_key / authorization 之类字段的值换成 [REDACTED] ───
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


# ── ⑧ 落盘调试产物：prompt / 最终回答 / 完整 result+trace（已脱敏）──────
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


# ── 主流程：①→⑧ 串起来跑一遍，再打印关键结果 ──────────────────────────
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
