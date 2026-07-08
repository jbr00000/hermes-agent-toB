"""Tests for the to-B cron scheduler contract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cron.scheduler import (
    _deliver_result,
    _merge_mcp_into_per_job_toolsets,
    _resolve_cron_enabled_toolsets,
    _resolve_delivery_target,
    _resolve_delivery_targets,
    _resolve_origin,
    _send_media_via_adapter,
    cron_delivery_targets,
)


class TestPerJobToolsetMcpMerge:
    CFG = {
        "mcp_servers": {
            "finnhub": {"enabled": True},
            "playwright": {"enabled": True},
            "disabled_one": {"enabled": False},
            "string_enabled": "ignored",
        }
    }

    def test_native_list_gets_enabled_mcp_servers(self):
        result = _merge_mcp_into_per_job_toolsets(["web", "terminal"], self.CFG)
        assert set(result) == {"web", "terminal", "finnhub", "playwright"}

    def test_no_mcp_sentinel_opts_out_and_is_stripped(self):
        assert _merge_mcp_into_per_job_toolsets(["web", "no_mcp"], self.CFG) == ["web"]

    def test_explicit_mcp_name_is_treated_as_allowlist(self):
        assert _merge_mcp_into_per_job_toolsets(["web", "finnhub"], self.CFG) == [
            "web",
            "finnhub",
        ]

    def test_resolver_uses_merge_for_per_job_lists(self):
        job = {"enabled_toolsets": ["web", "terminal"]}
        result = _resolve_cron_enabled_toolsets(job, self.CFG)
        assert set(result) == {"web", "terminal", "finnhub", "playwright"}

    def test_resolver_without_per_job_list_delegates_to_cron_platform(self):
        with patch("hermes_cli.tools_config._get_platform_tools", return_value={"web"}):
            assert _resolve_cron_enabled_toolsets({"enabled_toolsets": None}, self.CFG) == [
                "web"
            ]


class TestResolveOrigin:
    def test_full_origin_preserves_metadata(self):
        job = {
            "origin": {
                "platform": "api",
                "chat_id": "task-123",
                "user_id": "alice",
                "thread_id": "thread-1",
            }
        }
        assert _resolve_origin(job) == job["origin"]

    @pytest.mark.parametrize("origin", [None, {}, "cli", ["api", "task"], 42])
    def test_missing_or_non_dict_origin_returns_none(self, origin):
        assert _resolve_origin({"origin": origin}) is None

    def test_missing_required_origin_fields_returns_none(self):
        assert _resolve_origin({"origin": {"platform": "api"}}) is None
        assert _resolve_origin({"origin": {"chat_id": "task-123"}}) is None


class TestToBCronDeliveryContract:
    def test_platform_delivery_targets_are_disabled(self):
        job = {
            "deliver": "origin",
            "origin": {"platform": "telegram", "chat_id": "123"},
        }
        assert _resolve_delivery_target(job) is None
        assert _resolve_delivery_targets(job) == []
        assert cron_delivery_targets() == []

    def test_deliver_result_is_persist_only_noop(self):
        assert _deliver_result({"id": "job-1"}, "done", adapters={"telegram": object()}) is None

    def test_media_delivery_is_noop(self):
        assert _send_media_via_adapter(
            adapter=object(),
            chat_id="123",
            media_files=[Path("out.png")],
            metadata={"kind": "image"},
            loop=None,
            job={"id": "job-1"},
        ) is None


def test_mark_job_run_records_status_and_delivery_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from cron.jobs import create_job, load_jobs, mark_job_run

    job = create_job(prompt="ping", schedule="every 1h", name="Ping")
    mark_job_run(job["id"], success=False, error="boom", delivery_error="delivery off")

    stored = next(item for item in load_jobs() if item["id"] == job["id"])
    assert stored["last_status"] == "error"
    assert stored["last_error"] == "boom"
    assert stored["last_delivery_error"] == "delivery off"


def test_save_job_output_persists_local_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from cron.jobs import save_job_output

    output_path = Path(save_job_output("job-1", "hello"))
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == "hello"
