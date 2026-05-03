"""Tests for reporting/common.py — schema checks, data loading, HTML helpers."""
import json
import os
import tempfile
import pytest
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting.common import (
    EXPECTED_BACKUP_SCHEMA_VERSION,
    check_backup_schema,
    read_backup_meta,
    render_security_baseline,
    build_fallback_security_checks,
    _he,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ── Schema version checks ────────────────────────────────────────────────────

class TestCheckBackupSchema:
    def test_no_meta_returns_warning(self, tmp_path):
        warnings = check_backup_schema(str(tmp_path))
        assert len(warnings) == 1
        assert "backup_meta.json not found" in warnings[0]

    def test_current_version_no_warnings(self, tmp_path):
        meta = {"schema_version": EXPECTED_BACKUP_SCHEMA_VERSION, "org_name": "Test"}
        (tmp_path / "backup_meta.json").write_text(json.dumps(meta))
        warnings = check_backup_schema(str(tmp_path))
        assert warnings == []

    def test_older_version_returns_warning(self, tmp_path):
        meta = {"schema_version": EXPECTED_BACKUP_SCHEMA_VERSION - 1}
        (tmp_path / "backup_meta.json").write_text(json.dumps(meta))
        # Only produces a warning if EXPECTED > 1; if EXPECTED == 1 version 0 < 1
        if EXPECTED_BACKUP_SCHEMA_VERSION > 1:
            warnings = check_backup_schema(str(tmp_path))
            assert any("older" in w for w in warnings)

    def test_newer_version_returns_warning(self, tmp_path):
        meta = {"schema_version": EXPECTED_BACKUP_SCHEMA_VERSION + 1}
        (tmp_path / "backup_meta.json").write_text(json.dumps(meta))
        warnings = check_backup_schema(str(tmp_path))
        assert len(warnings) == 1
        assert "newer" in warnings[0]

    def test_missing_schema_version_field(self, tmp_path):
        meta = {"pipeline_version": "1.0"}
        (tmp_path / "backup_meta.json").write_text(json.dumps(meta))
        warnings = check_backup_schema(str(tmp_path))
        assert len(warnings) == 1
        assert "missing schema_version" in warnings[0]

    def test_corrupt_meta_returns_empty(self, tmp_path):
        (tmp_path / "backup_meta.json").write_text("{invalid json}")
        meta = read_backup_meta(str(tmp_path))
        assert meta == {}

    def test_fixture_meta_is_compatible(self):
        warnings = check_backup_schema(FIXTURES)
        assert warnings == [], f"Fixture meta triggered warnings: {warnings}"


# ── render_security_baseline ─────────────────────────────────────────────────

class TestRenderSecurityBaseline:
    def test_empty_returns_empty_string(self):
        assert render_security_baseline([]) == ""

    def test_pass_check_rendered(self):
        checks = [{"networkName": "Main", "check": "AMP", "status": "Pass",
                   "description": "AMP is on", "remediation": "Keep it on"}]
        html = render_security_baseline(checks)
        assert "AMP" in html
        assert "check-pass" in html
        assert "Main" in html

    def test_fail_check_uses_fail_class(self):
        checks = [{"networkName": "Main", "check": "IPS", "status": "Fail",
                   "description": "IPS off", "remediation": "Turn it on"}]
        html = render_security_baseline(checks)
        assert "check-fail" in html

    def test_warning_check_uses_warning_class(self):
        checks = [{"networkName": "Main", "check": "Spoof", "status": "Warning",
                   "description": "Partial", "remediation": "Fix it"}]
        html = render_security_baseline(checks)
        assert "check-warning" in html

    def test_xss_escaped_in_check_name(self):
        checks = [{"networkName": "Net", "check": "<script>alert(1)</script>",
                   "status": "Pass", "description": "", "remediation": ""}]
        html = render_security_baseline(checks)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ── build_fallback_security_checks ───────────────────────────────────────────

class TestBuildFallbackSecurityChecks:
    def test_returns_list(self):
        result = build_fallback_security_checks([], {}, [])
        assert isinstance(result, list)
        assert len(result) > 0

    def test_all_online_produces_pass(self):
        devices = [{"status": "online"}, {"status": "online"}]
        checks = build_fallback_security_checks(devices, {}, [])
        status_check = next(c for c in checks if c["check"] == "Device Online Status")
        assert status_check["status"] == "Pass"

    def test_offline_device_produces_fail(self):
        devices = [{"status": "online"}, {"status": "offline"}]
        checks = build_fallback_security_checks(devices, {}, [])
        status_check = next(c for c in checks if c["check"] == "Device Online Status")
        assert status_check["status"] == "Fail"

    def test_port_issues_flagged(self):
        port_issues = [{"port": "1", "errors": ["CRC error"], "switch": "X"}]
        checks = build_fallback_security_checks([], {}, port_issues)
        port_check = next((c for c in checks if c["check"] == "Port Security Configuration"), None)
        assert port_check is not None
        assert port_check["status"] == "Fail"


# ── _he HTML escaping ─────────────────────────────────────────────────────────

class TestHe:
    def test_escapes_lt_gt(self):
        assert "&lt;" in _he("<") and "&gt;" in _he(">")

    def test_escapes_ampersand(self):
        assert "&amp;" in _he("&")

    def test_safe_string_unchanged(self):
        assert _he("hello world") == "hello world"

    def test_empty_string(self):
        assert _he("") == ""
