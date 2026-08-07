from __future__ import annotations


def test_task_sandbox_key_is_stable_and_scoped(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")

    from server.sandbox import task_sandbox_key

    first = task_sandbox_key("user-a", "task-a")
    assert first == task_sandbox_key("user-a", "task-a")
    assert first.startswith("tob-")
    assert len(first) == 36
    assert first != task_sandbox_key("user-b", "task-a")
    assert first != task_sandbox_key("user-a", "task-b")


def test_task_sandbox_key_changes_between_tenants(monkeypatch) -> None:
    from server.sandbox import task_sandbox_key

    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")
    first = task_sandbox_key("user-a", "task-a")
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-b")
    assert first != task_sandbox_key("user-a", "task-a")


def test_release_retains_workspace_and_destroy_removes_it(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_TENANT_ID", "tenant-a")

    import tools.terminal_tool as terminal_tool
    import tools.environments.docker as docker_environment
    from server.sandbox import (
        destroy_task_sandbox,
        release_task_sandbox,
        task_sandbox_key,
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

    key = task_sandbox_key("user-a", "task-a")
    workspace = home / "sandboxes" / "docker" / key
    workspace.mkdir(parents=True)
    (workspace / "result.txt").write_text("retained", encoding="utf-8")

    release_task_sandbox("user-a", "task-a")
    assert workspace.exists()
    assert cleanup_calls == [(key, True)]
    assert remove_calls == [key]

    destroy_task_sandbox("user-a", "task-a")
    assert not workspace.exists()
    assert cleanup_calls == [(key, True), (key, True)]
    assert remove_calls == [key, key]
