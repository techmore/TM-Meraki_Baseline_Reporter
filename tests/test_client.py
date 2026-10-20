"""Tests for meraki_client.py with a fully mocked HTTP layer (no network, no credentials)."""

import io
import json
import urllib.error

import pytest

import meraki_client as mc
import meraki_query

KEY = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


class _FakeResponse:
    def __init__(self, payload, headers=None, status=200):
        self._body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.headers = headers or {}
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code, body, headers=None):
    return urllib.error.HTTPError(url, code, "err", headers or {}, io.BytesIO(body.encode("utf-8")))


class TestGetJson:
    def test_sends_key_header_and_parses_json(self, monkeypatch):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["headers"] = dict(req.header_items())
            seen["timeout"] = timeout
            return _FakeResponse([{"id": "1"}], headers={"Link": ""})

        monkeypatch.setattr(mc.urllib.request, "urlopen", fake_urlopen)
        result = mc.get_json("https://api.example/organizations", KEY, timeout=7)
        assert result["data"] == [{"id": "1"}]
        assert result["status"] == 200
        assert seen["headers"]["X-cisco-meraki-api-key"] == KEY
        assert seen["timeout"] == 7

    def test_http_error_is_structured_and_never_contains_key(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise _http_error(req.full_url, 401, json.dumps({"errors": ["Invalid API key", KEY]}))

        monkeypatch.setattr(mc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(mc.MerakiRequestError) as exc:
            mc.get_json(f"https://api.example/orgs?token={KEY}", KEY)
        err = exc.value
        assert err.status == 401
        assert "invalid or revoked API key" in str(err)
        assert KEY not in str(err)
        assert KEY not in err.body

    def test_network_error(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(mc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(mc.MerakiRequestError, match="Network error"):
            mc.get_json("https://api.example/organizations", KEY)


class TestPagedGet:
    def test_follows_link_header(self, monkeypatch):
        pages = {
            f"{mc.API_BASE}/organizations?perPage=500": (
                [{"id": "a"}],
                {"Link": '<https://api.example/organizations?startingAfter=a>; rel="next"'},
            ),
            "https://api.example/organizations?startingAfter=a": ([{"id": "b"}], {}),
        }
        calls = []

        def fake_get_json(url, api_key, timeout=None):
            calls.append(url)
            data, headers = pages[url]
            return {"data": data, "headers": headers, "status": 200}

        monkeypatch.setattr(mc, "get_json", fake_get_json)
        monkeypatch.setattr(mc.time, "sleep", lambda s: None)
        assert mc.paged_get("/organizations", KEY) == [{"id": "a"}, {"id": "b"}]
        assert len(calls) == 2

    def test_parse_link_header(self):
        links = mc.parse_link_header('<https://x/a>; rel="first", <https://x/b>; rel="next"')
        assert links == {"first": "https://x/a", "next": "https://x/b"}
        assert mc.parse_link_header(None) == {}


class TestMerakiQueryCli:
    def test_missing_key_exits_one_with_help(self, monkeypatch, capsys):
        monkeypatch.setattr(meraki_query, "require_api_key", lambda *a, **k: None)
        assert meraki_query.main([]) == 1

    def test_table_output(self, monkeypatch, capsys):
        monkeypatch.setattr(meraki_query, "require_api_key", lambda *a, **k: KEY)
        monkeypatch.setattr(meraki_query, "paged_get", lambda path, key: [{"id": "123", "name": "Acme"}])
        assert meraki_query.main(["--table"]) == 0
        out = capsys.readouterr().out
        assert "123" in out and "Acme" in out

    def test_check_mode_reports_failure_without_key(self, monkeypatch, capsys):
        monkeypatch.setattr(meraki_query, "require_api_key", lambda *a, **k: KEY)

        def boom(path, key):
            raise mc.MerakiRequestError("HTTP 401 (invalid or revoked API key)", status=401)

        monkeypatch.setattr(meraki_query, "paged_get", boom)
        assert meraki_query.main(["--check"]) == 1
        err = capsys.readouterr().err
        assert "HTTP 401" in err
        assert KEY not in err
        assert "****beef" in err

    def test_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            meraki_query.main(["--help"])
        assert exc.value.code == 0
        assert "never accepted on the command line" in capsys.readouterr().out
