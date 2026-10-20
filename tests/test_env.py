"""Tests for meraki_env.py — API key resolution, masking and log redaction.

No real credentials are used; keyring access is stubbed.
"""

import logging
import os

import pytest

import meraki_env as me

KEY = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv(me.ENV_KEY, raising=False)
    monkeypatch.delenv(me.ENV_KEY_FILE, raising=False)
    monkeypatch.setattr(me, "_keyring_get", lambda: None)
    monkeypatch.chdir(tmp_path)
    yield
    # load_env() writes into os.environ; make sure nothing leaks into later tests.
    os.environ.pop(me.ENV_KEY, None)
    os.environ.pop(me.ENV_KEY_FILE, None)


class TestResolveApiKey:
    def test_missing_everywhere(self):
        assert me.resolve_api_key() == (None, "missing")
        assert me.get_api_key() is None

    def test_environment_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(me.ENV_KEY, KEY)
        (tmp_path / ".env").write_text(f"{me.ENV_KEY}=other\n", encoding="utf-8")
        assert me.resolve_api_key() == (KEY, "env")

    def test_placeholder_in_environment_is_ignored(self, monkeypatch):
        monkeypatch.setenv(me.ENV_KEY, "your_key_here")
        assert me.resolve_api_key() == (None, "missing")

    def test_dotenv_file(self, tmp_path):
        (tmp_path / ".env").write_text(f'# comment\nexport {me.ENV_KEY}="{KEY}"\n', encoding="utf-8")
        assert me.resolve_api_key() == (KEY, ".env")

    def test_key_file(self, monkeypatch, tmp_path):
        secret = tmp_path / "secret"
        secret.write_text(KEY + "\n", encoding="utf-8")
        monkeypatch.setenv(me.ENV_KEY_FILE, str(secret))
        assert me.resolve_api_key() == (KEY, "file")

    def test_key_file_can_be_configured_from_dotenv(self, tmp_path):
        secret = tmp_path / "secret"
        secret.write_text(KEY, encoding="utf-8")
        (tmp_path / ".env").write_text(f"{me.ENV_KEY_FILE}={secret}\n", encoding="utf-8")
        assert me.resolve_api_key() == (KEY, "file")

    def test_keyring_fallback(self, monkeypatch):
        monkeypatch.setattr(me, "_keyring_get", lambda: KEY)
        assert me.resolve_api_key() == (KEY, "keyring")

    def test_require_api_key_prints_help_when_missing(self, capsys):
        assert me.require_api_key() is None
        err = capsys.readouterr().err
        assert "MERAKI_API_KEY" in err
        assert "keyring" in err


class TestMaskAndRedact:
    def test_mask_shows_only_last_four(self):
        assert me.mask_key(KEY) == "****4567"
        assert KEY[:8] not in me.mask_key(KEY)

    def test_mask_short_or_empty(self):
        assert me.mask_key("") == "(none)"
        assert me.mask_key("abc") == "****"

    def test_redact(self):
        assert me.redact(f"HTTP 401 for https://x?k={KEY}", KEY) == "HTTP 401 for https://x?k=***REDACTED***"
        assert me.redact("nothing", None) == "nothing"

    def test_redacting_writer(self):
        sink = []
        write = me.redacting_writer(KEY, sink.append)
        write(f"key={KEY}\n")
        assert sink == ["key=***REDACTED***\n"]


class TestLoggingRedaction:
    def test_filter_masks_key_in_message_and_args(self):
        logger = logging.getLogger("test.redaction")
        logger.propagate = False
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Capture()
        logger.addHandler(handler)
        me.install_redaction(KEY, logger)
        try:
            logger.warning("request failed: %s", f"Bearer {KEY}")
            logger.warning("plain message")
        finally:
            logger.removeHandler(handler)
        assert records[0] == "request failed: Bearer ***REDACTED***"
        assert records[1] == "plain message"
        assert all(KEY not in r for r in records)

    def test_install_redaction_without_key_is_noop(self):
        logger = logging.getLogger("test.redaction.noop")
        before = list(logger.filters)
        me.install_redaction(None, logger)
        assert logger.filters == before


class TestCli:
    def test_status_missing_exits_one(self, capsys):
        assert me._cli(["status"]) == 1
        assert "No Meraki API key found" in capsys.readouterr().err

    def test_status_reports_source_and_masked_key(self, monkeypatch, capsys):
        monkeypatch.setenv(me.ENV_KEY, KEY)
        assert me._cli(["status"]) == 0
        out = capsys.readouterr().out
        assert "****4567" in out
        assert "source: env" in out
        assert KEY not in out

    def test_store_uses_hidden_prompt_and_keyring(self, monkeypatch, capsys):
        stored = {}
        monkeypatch.setattr("getpass.getpass", lambda prompt="": KEY)
        monkeypatch.setattr(me, "_keyring_set", lambda value: stored.setdefault("v", value) or True)
        assert me._cli(["store"]) == 0
        assert stored["v"] == KEY
        assert KEY not in capsys.readouterr().out

    def test_store_rejects_placeholder(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "your_key_here")
        assert me._cli(["store"]) == 1

    def test_help_mentions_no_argv_key(self, capsys):
        with pytest.raises(SystemExit) as exc:
            me._cli(["--help"])
        assert exc.value.code == 0
        assert "shell history" in capsys.readouterr().out
