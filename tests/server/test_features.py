from __future__ import annotations


def test_host_terminal_flag_requires_break_glass(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_FEATURE_HOST_TERMINAL", "true")
    monkeypatch.delenv("HERMES_ALLOW_HOST_TERMINAL", raising=False)

    from server.features import get_features

    assert get_features()["host_terminal"] is False


def test_host_terminal_break_glass_sets_local_backend(monkeypatch, tmp_path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_FEATURE_HOST_TERMINAL", "true")
    monkeypatch.setenv("HERMES_ALLOW_HOST_TERMINAL", "1")

    from server.features import apply_terminal_backend, get_features

    assert get_features()["host_terminal"] is True
    apply_terminal_backend()
    assert __import__("os").environ["TERMINAL_ENV"] == "local"


def test_browser_automation_flag_defaults_off_and_resolves_without_break_glass(
    monkeypatch, tmp_path
) -> None:
    """browser_automation 是普通 flag（无 break-glass）：默认关，env 可开。

    与 host_terminal 不同，它不授予宿主机权限——浏览器跑在独立容器，
    内网目标由 browser.allowed_targets 白名单收口。
    """
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_FEATURE_BROWSER_AUTOMATION", raising=False)

    from server.features import get_features

    assert get_features()["browser_automation"] is False

    monkeypatch.setenv("HERMES_FEATURE_BROWSER_AUTOMATION", "true")
    assert get_features()["browser_automation"] is True
