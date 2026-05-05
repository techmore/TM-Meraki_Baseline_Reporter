"""Tests for meraki_backup.py helpers — cache logic, write_json, schema constants."""
import json
import os
import sys
import time
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import meraki_backup as mb
import meraki_client as mc


# ── _cache_is_fresh ───────────────────────────────────────────────────────────

class TestCacheIsFresh:
    def test_nonexistent_file_returns_false(self, tmp_path):
        assert mb._cache_is_fresh(str(tmp_path / "missing.json")) is False

    def test_fresh_valid_json_returns_true(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"key": "value"}))
        assert mb._cache_is_fresh(str(p), max_age_h=12) is True

    def test_force_returns_false_even_when_fresh(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({}))
        assert mb._cache_is_fresh(str(p), max_age_h=12, force=True) is False

    def test_corrupt_json_returns_false(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json}")
        assert mb._cache_is_fresh(str(p), max_age_h=12) is False

    def test_old_file_returns_false(self, tmp_path):
        p = tmp_path / "old.json"
        p.write_text(json.dumps({"x": 1}))
        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(str(p), (old_time, old_time))
        assert mb._cache_is_fresh(str(p), max_age_h=24) is False

    def test_within_max_age_returns_true(self, tmp_path):
        p = tmp_path / "recent.json"
        p.write_text(json.dumps({"x": 1}))
        # Set mtime to 5 hours ago
        recent_time = time.time() - (5 * 3600)
        os.utime(str(p), (recent_time, recent_time))
        assert mb._cache_is_fresh(str(p), max_age_h=12) is True


# ── write_json / _load_json_file ──────────────────────────────────────────────

class TestWriteAndLoad:
    def test_roundtrip(self, tmp_path):
        payload = {"hello": "world", "nums": [1, 2, 3]}
        p = str(tmp_path / "test.json")
        mb.write_json(p, payload)
        loaded = mb._load_json_file(p)
        assert loaded == payload

    def test_creates_file(self, tmp_path):
        p = str(tmp_path / "new.json")
        assert not os.path.exists(p)
        mb.write_json(p, {})
        assert os.path.exists(p)


class TestGranularArtifacts:
    def test_artifact_path_is_namespaced(self, tmp_path):
        path = mb._artifact_path(str(tmp_path), "switches", "Q2XX-1/2", "port_statuses.json")
        assert path.endswith("switches/Q2XX-1_2/port_statuses.json")

    def test_granular_roundtrip(self, tmp_path):
        payload = {"hello": "world"}
        mb._write_granular_json(str(tmp_path), "networks", "N_123", "clients.json", payload)
        loaded = mb._read_granular_json(str(tmp_path), "networks", "N_123", "clients.json")
        assert loaded == payload

    def test_granular_cache_fresh_uses_underlying_file(self, tmp_path):
        mb._write_granular_json(str(tmp_path), "switches", "SW1", "lldp_cdp.json", {"x": 1})
        assert mb._granular_cache_fresh(str(tmp_path), "switches", "SW1", "lldp_cdp.json", max_age_h=12) is True


# ── Schema version constant ───────────────────────────────────────────────────

class TestSchemaVersion:
    def test_schema_version_is_int(self):
        assert isinstance(mb.BACKUP_SCHEMA_VERSION, int)

    def test_schema_version_positive(self):
        assert mb.BACKUP_SCHEMA_VERSION >= 1

    def test_pipeline_version_is_string(self):
        assert isinstance(mb.PIPELINE_VERSION, str)


class TestClientSummaries:
    def test_ap_client_summary_ignores_wired_clients(self):
        summary = mb.summarize_ap_clients(
            {
                "N_1": [
                    {"recentDeviceConnection": "Wireless", "recentDeviceSerial": "AP1"},
                    {"recentDeviceConnection": "Wireless", "recentDeviceSerial": "AP1"},
                    {"recentDeviceConnection": "Wired", "recentDeviceSerial": "SW1"},
                ]
            }
        )

        assert summary["ap_client_counts"] == [("AP1", 2)]


class TestPagedGetRateLimit:
    def test_retry_after_header_is_honored(self, monkeypatch):
        sleeps = []
        calls = {"count": 0}

        def fake_get_json(url, api_key, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise mc.MerakiRequestError(
                    "HTTP 429 for test",
                    status=429,
                    headers={"Retry-After": "7"},
                )
            return {"data": [], "headers": {}, "status": 200}

        monkeypatch.setattr(mc, "get_json", fake_get_json)
        monkeypatch.setattr(mc.time, "sleep", lambda seconds: sleeps.append(seconds))

        result = mb.paged_get("/organizations", "test-key")
        assert result == []
        assert calls["count"] == 2
        assert sleeps[0] == 7

    def test_retry_after_falls_back_when_header_missing(self, monkeypatch):
        sleeps = []
        calls = {"count": 0}

        def fake_get_json(url, api_key, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise mc.MerakiRequestError("HTTP 429 for test", status=429)
            return {"data": [], "headers": {}, "status": 200}

        monkeypatch.setattr(mc, "get_json", fake_get_json)
        monkeypatch.setattr(mc.time, "sleep", lambda seconds: sleeps.append(seconds))

        result = mb.paged_get("/organizations", "test-key")
        assert result == []
        assert calls["count"] == 2
        assert sleeps[0] == 2


class TestSharedMerakiClient:
    def test_build_url_appends_query_params(self):
        url = mc.build_url("/organizations", {"perPage": 5, "foo": "bar"})
        assert url.startswith("https://api.meraki.com/api/v1/organizations?")
        assert "perPage=5" in url
        assert "foo=bar" in url

    def test_shared_paged_get_honors_retry_after(self, monkeypatch):
        sleeps = []
        calls = {"count": 0}

        def fake_get_json(url, api_key, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise mc.MerakiRequestError(
                    "HTTP 429 for test",
                    status=429,
                    headers={"Retry-After": "3"},
                )
            return {"data": [], "headers": {}, "status": 200}

        monkeypatch.setattr(mc, "get_json", fake_get_json)
        monkeypatch.setattr(mc.time, "sleep", lambda seconds: sleeps.append(seconds))

        result = mc.paged_get("/organizations", "test-key")
        assert result == []
        assert calls["count"] == 2
        assert sleeps[0] == 3


# ── summarize_availabilities ──────────────────────────────────────────────────

class TestSummarizeAvailabilities:
    def _make_devices(self, statuses):
        return [{"status": s, "productType": "switch", "serial": f"S{i}"}
                for i, s in enumerate(statuses)]

    def test_all_online(self):
        devs = self._make_devices(["online", "online", "online"])
        result = mb.summarize_availabilities(devs)
        assert result.get("offline_count") == 0
        assert result.get("total") == 3

    def test_one_offline(self):
        devs = self._make_devices(["online", "online", "offline"])
        result = mb.summarize_availabilities(devs)
        assert result.get("offline_count") == 1
        assert len(result.get("offline_devices", [])) == 1

    def test_empty_returns_dict(self):
        result = mb.summarize_availabilities([])
        assert isinstance(result, dict)


# ── summarize_inventory ───────────────────────────────────────────────────────

class TestSummarizeInventory:
    def test_counts_by_type(self):
        inventory = [
            {"productType": "appliance", "model": "MX68", "serial": "A"},
            {"productType": "switch", "model": "MS225", "serial": "B"},
            {"productType": "switch", "model": "MS225", "serial": "C"},
            {"productType": "wireless", "model": "MR46", "serial": "D"},
        ]
        result = mb.summarize_inventory(inventory)
        assert result["by_type"]["appliance"] == 1
        assert result["by_type"]["switch"] == 2
        assert result["by_type"]["wireless"] == 1

    def test_top_models_present(self):
        inventory = [
            {"productType": "switch", "model": "MS225", "serial": "A"},
            {"productType": "switch", "model": "MS225", "serial": "B"},
            {"productType": "wireless", "model": "MR46", "serial": "C"},
        ]
        result = mb.summarize_inventory(inventory)
        models = [m[0] for m in result.get("top_models", [])]
        assert "MS225" in models


class TestRecommendSwitchPorts:
    def test_disconnected_access_port_messages_are_not_findings(self):
        result = mb.recommend_switch_ports(
            {
                "SW1": [
                    {
                        "portId": "1",
                        "status": "Disconnected",
                        "isUplink": False,
                        "errors": ["Port disconnected", "No link detected"],
                        "warnings": ["Link down"],
                    },
                    {
                        "portId": "2",
                        "status": "Connected",
                        "isUplink": False,
                        "errors": ["CRC errors detected"],
                        "warnings": [],
                    },
                ]
            },
            {"SW1": [{"portId": "1", "enabled": True}, {"portId": "2", "enabled": True}]},
        )

        findings = result["switch_port_findings"]
        assert len(findings) == 1
        assert findings[0]["portId"] == "2"
        assert findings[0]["detail"] == "CRC errors detected"
