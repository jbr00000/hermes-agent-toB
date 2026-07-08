from types import SimpleNamespace

from hermes_cli.status import show_status


def _quiet_status_dependencies(monkeypatch, tmp_path):
    from hermes_cli import status as status_mod
    import hermes_cli.auth as auth_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status_mod, "get_env_path", lambda: tmp_path / ".env", raising=False)
    monkeypatch.setattr(status_mod, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(status_mod, "load_config", lambda: {"model": "deepseek-chat"}, raising=False)
    monkeypatch.setattr(
        status_mod,
        "resolve_requested_provider",
        lambda requested=None: "openai-compatible",
        raising=False,
    )
    monkeypatch.setattr(
        status_mod,
        "resolve_provider",
        lambda requested=None, **kwargs: "openai-compatible",
        raising=False,
    )
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: "OpenAI-compatible", raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_qwen_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_minimax_oauth_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(status_mod, "managed_nous_tools_enabled", lambda: False, raising=False)
    return status_mod


def test_show_status_all_does_not_print_tavily_key_value(monkeypatch, capsys, tmp_path):
    _quiet_status_dependencies(monkeypatch, tmp_path)
    sentinel = "NONSECRET_SENTINEL_VALUE_DO_NOT_PRINT_123456"
    monkeypatch.setenv("TAVILY_API_KEY", sentinel)

    show_status(SimpleNamespace(all=True, deep=False))

    output = capsys.readouterr().out
    assert "Tavily" in output
    assert sentinel not in output


def test_show_status_reports_tob_gateway_removed(monkeypatch, capsys, tmp_path):
    status_mod = _quiet_status_dependencies(monkeypatch, tmp_path)

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Messaging Platforms" in output
    assert "Gateway Service" in output
    assert "removed in to-B build" in output
    assert "enterprise API/front-end access layer" in output
    assert "API/front-end orchestration replaces messaging gateway" in output
    assert "Start with:" not in output


def test_show_status_reports_nous_auth_error(monkeypatch, capsys, tmp_path):
    status_mod = _quiet_status_dependencies(monkeypatch, tmp_path)
    import hermes_cli.auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "get_nous_auth_status",
        lambda: {
            "logged_in": False,
            "portal_base_url": "https://portal.nousresearch.com",
            "access_expires_at": "2026-04-20T01:00:51+00:00",
            "agent_key_expires_at": "2026-04-20T04:54:24+00:00",
            "has_refresh_token": True,
            "error": "Refresh session has been revoked",
        },
        raising=False,
    )

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Nous Portal" in output
    assert "not logged in (run: hermes portal)" in output
    assert "Error:      Refresh session has been revoked" in output
    assert "Access exp:" in output
    assert "Key exp:" in output
