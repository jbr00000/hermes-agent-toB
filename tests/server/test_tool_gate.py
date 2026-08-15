"""controlled 权限档审批门控钩子（server.tool_gate）的单元测试。"""
from __future__ import annotations

import threading
import time

import pytest

from server import tool_gate
from server.storage.runtime import get_runtime_store, reset_runtime_store_for_tests

IDS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DATABASE_URL", raising=False)
    monkeypatch.delenv("HERMES_REDIS_URL", raising=False)
    monkeypatch.delenv("HERMES_TOOL_APPROVAL_TIMEOUT_SECONDS", raising=False)

    from server import auth
    from server.storage import (
        get_repository,
        init_storage,
        reset_storage_for_tests,
    )

    auth._JWT_SECRET = None
    reset_storage_for_tests()
    reset_runtime_store_for_tests()
    init_storage()
    repository = get_repository()
    user = repository.create_user("gate-user", "hash", "user")
    task = repository.create_agent_task(user["id"], "gate task")
    # list_tool_approvals 有属主检查，门控行必须挂在真实存在的 user/task 下。
    IDS.update(
        user_id=user["id"],
        task_id=task["id"],
        session_id=task["session_id"],
    )
    yield
    IDS.clear()
    tool_gate._gates.clear()
    reset_runtime_store_for_tests()
    reset_storage_for_tests()


def _register_gate(events: list, **overrides) -> dict:
    gate_kwargs = {
        "session_id": IDS["session_id"],
        "request_id": "run-1",
        "task_id": IDS["task_id"],
        "user_id": IDS["user_id"],
        "permission_mode": "controlled",
        "emit": lambda event, data: events.append({"event": event, "data": data}) or 0,
    }
    gate_kwargs.update(overrides)
    tool_gate.register_run_gate(**gate_kwargs)
    return gate_kwargs


def _run_hook_in_thread(**hook_kwargs) -> tuple[list, threading.Thread]:
    result: list = []
    kwargs = {
        "tool_name": "terminal",
        "args": {"command": "rm -rf /tmp/x"},
        "session_id": IDS["session_id"],
    }
    kwargs.update(hook_kwargs)
    thread = threading.Thread(
        target=lambda: result.append(tool_gate.tool_approval_gate_hook(**kwargs)),
        daemon=True,
    )
    thread.start()
    return result, thread


def _wait_for_pending_approval(timeout: float = 10.0) -> dict:
    from server.storage import get_repository

    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = get_repository().list_tool_approvals(
            IDS["user_id"], IDS["task_id"], status="pending"
        )
        if rows:
            return rows[0]
        time.sleep(0.05)
    raise AssertionError("no pending approval row appeared")


def _wait_for_event(events: list, name: str, timeout: float = 10.0) -> dict:
    """审批行落库与 emit 之间有空窗，事件必须轮询等待而不是直接索引。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in events:
            if event["event"] == name:
                return event
        time.sleep(0.05)
    raise AssertionError(f"event {name} never emitted; saw {events!r}")


def test_non_gated_tool_passes_without_gate() -> None:
    events: list = []
    _register_gate(events)
    assert (
        tool_gate.tool_approval_gate_hook(
            tool_name="db_query", args={"sql": "select 1"}, session_id=IDS["session_id"]
        )
        is None
    )
    assert events == []


def test_no_gate_registered_passes() -> None:
    assert (
        tool_gate.tool_approval_gate_hook(
            tool_name="terminal", args={"command": "ls"}, session_id="unknown"
        )
        is None
    )


def test_non_controlled_mode_passes() -> None:
    events: list = []
    _register_gate(events, permission_mode="read")
    assert (
        tool_gate.tool_approval_gate_hook(
            tool_name="terminal", args={"command": "ls"}, session_id=IDS["session_id"]
        )
        is None
    )
    assert events == []


def test_allow_all_flag_short_circuits() -> None:
    events: list = []
    _register_gate(events)
    get_runtime_store().set_run_flag("run-1", "allow_all")
    assert (
        tool_gate.tool_approval_gate_hook(
            tool_name="terminal", args={"command": "ls"}, session_id=IDS["session_id"]
        )
        is None
    )
    assert events == []


def test_approval_granted_lets_command_run() -> None:
    events: list = []
    _register_gate(events)
    result, thread = _run_hook_in_thread()

    approval = _wait_for_pending_approval()
    assert approval["tool_name"] == "terminal"
    assert approval["command_preview"] == "rm -rf /tmp/x"
    required = _wait_for_event(events, "tool.approval_required")
    assert required["data"]["id"] == approval["id"]

    from server.storage import get_repository

    get_repository().decide_tool_approval(
        approval["id"], user_id=IDS["user_id"], decision="approved"
    )
    thread.join(timeout=10)
    assert result == [None]
    assert events[-1]["event"] == "tool.approval_resolved"
    assert events[-1]["data"]["status"] == "approved"


def test_denial_blocks_with_sentinel() -> None:
    events: list = []
    _register_gate(events)
    result, thread = _run_hook_in_thread()

    approval = _wait_for_pending_approval()
    from server.storage import get_repository

    get_repository().decide_tool_approval(
        approval["id"], user_id=IDS["user_id"], decision="denied"
    )
    thread.join(timeout=10)
    assert result[0]["action"] == "block"
    assert result[0]["message"].startswith(tool_gate.DENIED_SENTINEL)
    assert events[-1]["data"]["status"] == "denied"

    # 拒绝原因有审计，且审计不含原始命令。
    audits = get_repository().list_audit_events(conversation_id=IDS["session_id"])
    approval_audits = [row for row in audits if row["event_type"] == "tool_approval"]
    assert {row["status"] for row in approval_audits} == {"pending", "denied"}
    for row in approval_audits:
        assert "rm -rf" not in str(row.get("metadata"))


def test_timeout_expires_and_blocks(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TOOL_APPROVAL_TIMEOUT_SECONDS", "2")
    events: list = []
    _register_gate(events)
    started = time.time()
    outcome = tool_gate.tool_approval_gate_hook(
        tool_name="terminal", args={"command": "ls"}, session_id=IDS["session_id"]
    )
    assert time.time() - started < 10
    assert outcome["action"] == "block"
    assert outcome["message"].startswith(tool_gate.DENIED_SENTINEL)
    assert "超时" in outcome["message"]
    assert events[-1]["data"]["status"] == "expired"


def test_cancellation_during_wait_expires_and_blocks() -> None:
    events: list = []
    _register_gate(events)
    result, thread = _run_hook_in_thread()
    _wait_for_pending_approval()
    get_runtime_store().cancel_request("run-1")
    thread.join(timeout=10)
    assert result[0]["action"] == "block"
    assert events[-1]["data"]["status"] == "expired"


def test_hook_failure_fails_closed(monkeypatch) -> None:
    events: list = []
    _register_gate(events)

    def _boom():
        raise RuntimeError("repository down")

    monkeypatch.setattr("server.storage.get_repository", _boom)
    outcome = tool_gate.tool_approval_gate_hook(
        tool_name="terminal", args={"command": "ls"}, session_id=IDS["session_id"]
    )
    assert outcome["action"] == "block"
    assert outcome["message"].startswith(tool_gate.DENIED_SENTINEL)


def test_process_tool_is_gated_and_preview_falls_back() -> None:
    events: list = []
    _register_gate(events)
    result, thread = _run_hook_in_thread(
        tool_name="process", args={"action": "list"}
    )
    approval = _wait_for_pending_approval()
    assert approval["command_preview"] == "process:list"

    from server.storage import get_repository

    get_repository().decide_tool_approval(
        approval["id"], user_id=IDS["user_id"], decision="approved"
    )
    thread.join(timeout=10)
    assert result == [None]
