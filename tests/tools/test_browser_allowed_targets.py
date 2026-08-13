"""Tests for the browser.allowed_targets intranet whitelist (P3 浏览器兜底).

P3 的场景是"操作客户系统"，目标通常在内网——恰是 SSRF 默认拦截的地址。
``browser.allowed_targets``（config.yaml）声明允许导航的内网主机/网段；
白名单只解除私网拦截，不解除云元数据地板（169.254.169.254 永远拦截）。
"""

import json

import pytest

from tools import browser_tool


@pytest.fixture(autouse=True)
def _reset_allowed_targets_cache(monkeypatch):
    """Each test sets its own whitelist via _get_allowed_targets."""
    monkeypatch.setattr(browser_tool, "_allowed_targets_resolved", True)
    monkeypatch.setattr(browser_tool, "_cached_allowed_targets", ())
    yield


class TestAllowedTargetMatching:
    def _match(self, url, targets):
        browser_tool._cached_allowed_targets = tuple(targets)
        return browser_tool._allowed_browser_target(url)

    def test_empty_whitelist_allows_nothing(self):
        assert self._match("http://10.0.1.5/oa", []) is False

    def test_exact_host_and_ip(self):
        targets = ["oa.example.com", "192.168.1.1"]
        assert self._match("http://oa.example.com/login", targets) is True
        assert self._match("http://oa.example.com:8080/x", targets) is True
        assert self._match("http://192.168.1.1/admin", targets) is True
        assert self._match("http://192.168.1.2/admin", targets) is False
        # 精确域名不含子域，且前缀混淆不命中
        assert self._match("http://sub.oa.example.com/x", targets) is False
        assert self._match("http://evil-oa.example.com/x", targets) is False

    def test_suffix_entry_matches_subdomains_and_bare_domain(self):
        targets = [".internal.corp"]
        assert self._match("http://hr.internal.corp/x", targets) is True
        assert self._match("http://internal.corp/x", targets) is True
        assert self._match("http://internal.corp.evil.com/x", targets) is False

    def test_cidr_entry_matches_ip_literals(self):
        targets = ["10.0.1.0/24"]
        assert self._match("http://10.0.1.20/oa", targets) is True
        assert self._match("http://10.0.2.20/oa", targets) is False
        # 域名主机不做 CIDR 反解（不触发 DNS）
        assert self._match("http://oa.example.com/", targets) is False


class TestNavigateWhitelistIntegration:
    """浏览器走 CDP/云后端（非本地）时，私网地址默认被拦；白名单命中放行。"""

    OA_URL = "http://mock-oa/page.html"

    @pytest.fixture()
    def _cloud_mode_patches(self, monkeypatch):
        monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
        monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
        monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: False)
        monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: False)
        monkeypatch.setattr(
            browser_tool,
            "_get_session_info",
            lambda task_id: {
                "session_name": f"s_{task_id}",
                "bb_session_id": None,
                "cdp_url": None,
                "features": {"local": True},
                "_first_nav": False,
            },
        )
        monkeypatch.setattr(
            browser_tool,
            "_run_browser_command",
            lambda *a, **kw: {"success": True, "data": {"title": "OK", "url": self.OA_URL}},
        )

    def test_private_url_blocked_when_not_whitelisted(self, monkeypatch, _cloud_mode_patches):
        browser_tool._cached_allowed_targets = ()
        result = json.loads(browser_tool.browser_navigate(self.OA_URL, task_id="t1"))
        assert result["success"] is False
        assert "private or internal" in result["error"]
        assert "allowed_targets" in result["error"]  # 提示运维入口

    def test_private_url_allowed_when_whitelisted(self, monkeypatch, _cloud_mode_patches):
        browser_tool._cached_allowed_targets = ("mock-oa",)
        result = json.loads(browser_tool.browser_navigate(self.OA_URL, task_id="t2"))
        assert result["success"] is True

    def test_metadata_floor_not_lifted_by_whitelist(self, monkeypatch, _cloud_mode_patches):
        """即使把元数据地址加进白名单，永远拦截地板仍然生效。"""
        browser_tool._cached_allowed_targets = ("169.254.169.254",)
        result = json.loads(
            browser_tool.browser_navigate(
                "http://169.254.169.254/latest/meta-data", task_id="t3"
            )
        )
        assert result["success"] is False
        assert "cloud metadata" in result["error"]
