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


def test_tob_server_enforces_deployment_resource_and_mount_policy(monkeypatch, tmp_path) -> None:
    deployment = tmp_path / "deployment.yaml"
    deployment.write_text(
        """
sandbox:
  network_egress: allowlist
  allowed_hosts: [api.internal]
  cpu_limit: "1.5"
  memory_limit: 2g
  pids_limit: 96
  timeout_seconds: 240
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DEPLOYMENT_CONFIG", str(deployment))
    monkeypatch.setenv("HERMES_TOB_SERVER", "1")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_CONTAINER_CPU", "not-a-number")
    monkeypatch.setenv("TERMINAL_CONTAINER_MEMORY", "not-a-number")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "not-a-number")
    monkeypatch.setenv("TERMINAL_DOCKER_NETWORK", "true")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", "not-json")
    monkeypatch.setenv("TERMINAL_DOCKER_FORWARD_ENV", "not-json")
    monkeypatch.setenv("TERMINAL_DOCKER_EXTRA_ARGS", "not-json")
    monkeypatch.setenv("TERMINAL_DOCKER_RUN_AS_HOST_USER", "true")

    import tools.terminal_tool as terminal_tool

    config = terminal_tool._get_env_config()

    assert config["container_cpu"] == 1.5
    assert config["container_memory"] == 2048
    assert config["container_pids_limit"] == 96
    assert config["timeout"] == 240
    assert config["docker_network"] is False
    assert config["docker_volumes"] == []
    assert config["docker_forward_env"] == []
    assert config["docker_extra_args"] == []
    assert config["docker_allow_sensitive_host_mounts"] is False
    assert config["docker_run_as_host_user"] is False

    backend_config = terminal_tool._container_config_from_env(config)
    assert backend_config["docker_network"] is False
    assert backend_config["container_pids_limit"] == 96
    assert backend_config["docker_allow_sensitive_host_mounts"] is False
