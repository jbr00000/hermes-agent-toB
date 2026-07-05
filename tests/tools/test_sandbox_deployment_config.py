from __future__ import annotations


def test_deployment_default_denies_docker_network(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_ENV", "docker")

    import tools.terminal_tool as terminal_tool

    config = terminal_tool._get_env_config()
    captured: dict[str, object] = {}

    class FakeDockerEnvironment:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(terminal_tool, "_DockerEnvironment", FakeDockerEnvironment)
    monkeypatch.setattr(terminal_tool, "_maybe_reap_docker_orphans", lambda _cc: None)

    terminal_tool._create_environment(
        "docker",
        "python:3.12",
        "/root",
        60,
        container_config=config,
        task_id="s1",
    )

    assert captured["network"] is False
