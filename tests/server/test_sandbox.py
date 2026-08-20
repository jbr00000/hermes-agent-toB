from __future__ import annotations

import pytest


def test_user_sandbox_key_is_stable_and_scoped(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")

    from server.sandbox import task_sandbox_key, user_sandbox_key

    first = user_sandbox_key("user-a")
    assert first == user_sandbox_key("user-a")
    assert first.startswith("tob-")
    assert len(first) == 36
    assert first != user_sandbox_key("user-b")
    # 沙箱粒度是用户：同一用户的所有任务共用同一个 key
    assert task_sandbox_key("user-a", "task-a") == first
    assert task_sandbox_key("user-a", "task-b") == first


def test_user_sandbox_key_changes_between_tenants(monkeypatch) -> None:
    from server.sandbox import user_sandbox_key

    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")
    first = user_sandbox_key("user-a")
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-b")
    assert first != user_sandbox_key("user-a")


def test_task_workspace_dir_containment_and_validation(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")

    from server.sandbox import (
        task_container_cwd,
        task_workspace_dir,
        user_sandbox_key,
        user_workspace_dir,
    )

    key = user_sandbox_key("user-a")
    workspace = task_workspace_dir("user-a", "task-a")
    assert workspace == home / "sandboxes" / "docker" / key / "workspace" / "tasks" / "task-a"
    assert workspace.is_dir()
    assert user_workspace_dir("user-a") == home / "sandboxes" / "docker" / key / "workspace"
    assert task_container_cwd("task-a") == "/workspace/tasks/task-a"

    # 不同任务互不覆盖，但都落在同一用户工作区下
    other = task_workspace_dir("user-a", "task-b")
    assert other != workspace
    assert other.parent.parent == workspace.parent.parent

    for bad in ("..", "a/b", "a\\b", ".hidden", "", "x" * 65):
        with pytest.raises(ValueError):
            task_workspace_dir("user-a", bad)
        with pytest.raises(ValueError):
            task_container_cwd(bad)


def test_release_is_noop_and_destroy_only_removes_task_subdir(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")

    import tools.terminal_tool as terminal_tool
    import tools.environments.docker as docker_environment
    from server.sandbox import (
        destroy_task_sandbox,
        destroy_user_sandbox,
        release_task_sandbox,
        task_workspace_dir,
        user_sandbox_key,
    )

    cleanup_calls: list[tuple[str, bool]] = []
    remove_calls: list[str] = []
    monkeypatch.setattr(
        terminal_tool,
        "cleanup_vm",
        lambda key, force_remove=False: cleanup_calls.append((key, force_remove)),
    )
    monkeypatch.setattr(
        docker_environment,
        "remove_task_containers",
        lambda key: remove_calls.append(key),
    )

    key = user_sandbox_key("user-a")
    sandbox_dir = home / "sandboxes" / "docker" / key
    task_a = task_workspace_dir("user-a", "task-a")
    task_b = task_workspace_dir("user-a", "task-b")
    (task_a / "result.txt").write_text("retained", encoding="utf-8")
    (task_b / "other.txt").write_text("untouched", encoding="utf-8")

    # 运行结束不回收：容器不动，工作区原样保留
    release_task_sandbox("user-a", "task-a")
    assert (task_a / "result.txt").read_text(encoding="utf-8") == "retained"
    assert cleanup_calls == []
    assert remove_calls == []

    # 删除任务只清该任务的子目录：共享容器与兄弟任务目录都不动
    destroy_task_sandbox("user-a", "task-a")
    assert not task_a.exists()
    assert (task_b / "other.txt").read_text(encoding="utf-8") == "untouched"
    assert sandbox_dir.exists()
    assert cleanup_calls == []
    assert remove_calls == []

    # 删除用户才整体回收：容器 + 整个工作区
    destroy_user_sandbox("user-a")
    assert not sandbox_dir.exists()
    assert cleanup_calls == [(key, True)]
    assert remove_calls == [key]

    # 幂等：不存在的任务/用户目录静默跳过
    destroy_task_sandbox("user-a", "task-a")
    destroy_user_sandbox("user-a")
    assert cleanup_calls == [(key, True), (key, True)]
    assert remove_calls == [key, key]
