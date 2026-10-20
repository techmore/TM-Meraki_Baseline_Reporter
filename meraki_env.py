"""Environment and credential helpers for the Meraki reporting pipeline.

The Meraki API key is resolved from, in order:

1. ``MERAKI_API_KEY`` already exported in the environment.
2. ``MERAKI_API_KEY`` in a local ``.env`` file (never committed).
3. ``MERAKI_API_KEY_FILE`` pointing at a file whose contents are the key
   (works with Docker/Kubernetes secrets and most secret managers).
4. The OS keyring (``pip install keyring``), service ``meraki-baseline-reporter``.

The key is never accepted on the command line (it would show up in shell
history and ``ps`` output) and is never written to logs: ``install_redaction``
masks it in every log record, and ``mask_key`` is used wherever a hint is shown.

Run ``python meraki_env.py --help`` to store or inspect the key interactively.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable, Optional

ENV_KEY = "MERAKI_API_KEY"
ENV_KEY_FILE = "MERAKI_API_KEY_FILE"
KEYRING_SERVICE = "meraki-baseline-reporter"
KEYRING_USERNAME = ENV_KEY
PLACEHOLDER_KEYS = frozenset({"", "your_key_here", "REPLACEME", "changeme"})
REDACTED = "***REDACTED***"


def load_env(path: str = ".env") -> None:
    """Load ``KEY=value`` lines from *path* into ``os.environ`` without overriding."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        return


def _is_real_key(value: Optional[str]) -> bool:
    return bool(value) and value.strip() not in PLACEHOLDER_KEYS


def _keyring_get() -> Optional[str]:
    """Return the key stored in the OS keyring, or None if unavailable."""
    try:
        import keyring  # type: ignore
    except Exception:  # keyring is optional
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        return None


def _keyring_set(value: str) -> bool:
    try:
        import keyring  # type: ignore

        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value)
        return True
    except Exception:
        return False


def _read_key_file(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def resolve_api_key(env_path: str = ".env") -> tuple[Optional[str], str]:
    """Return ``(key, source)``; *source* names where the key came from.

    *source* is one of ``"env"``, ``".env"``, ``"file"``, ``"keyring"`` or
    ``"missing"`` and is safe to print.
    """
    value = os.environ.get(ENV_KEY)
    if _is_real_key(value):
        return value.strip(), "env"

    before = ENV_KEY in os.environ
    load_env(env_path)
    value = os.environ.get(ENV_KEY)
    if _is_real_key(value):
        return value.strip(), "env" if before else ".env"

    key_file = os.environ.get(ENV_KEY_FILE)
    if key_file:
        value = _read_key_file(os.path.expanduser(key_file))
        if _is_real_key(value):
            return value, "file"

    value = _keyring_get()
    if _is_real_key(value):
        return value.strip(), "keyring"

    return None, "missing"


def get_api_key(env_path: str = ".env") -> Optional[str]:
    """Return the Meraki API key or ``None``; see module docstring for sources."""
    key, _source = resolve_api_key(env_path)
    return key


MISSING_KEY_HELP = f"""\
No Meraki API key found. Provide it in one of these ways (never on the command line):

  export {ENV_KEY}=...              # shell environment
  echo '{ENV_KEY}=...' >> .env      # local .env file (git-ignored)
  export {ENV_KEY_FILE}=/run/secrets/meraki   # file containing only the key
  python meraki_env.py store         # OS keyring (requires: pip install keyring)

Generate a key in Meraki Dashboard: Organization > Settings > Dashboard API access."""


def require_api_key(env_path: str = ".env", stream=None) -> Optional[str]:
    """Like :func:`get_api_key`, but prints actionable help when the key is missing."""
    key = get_api_key(env_path)
    if key is None:
        print(MISSING_KEY_HELP, file=stream or sys.stderr)
    return key


def mask_key(key: Optional[str]) -> str:
    """Return a display-safe hint such as ``****c3f9`` (never the full key)."""
    if not key:
        return "(none)"
    key = key.strip()
    if len(key) <= 8:
        return "****"
    return "****" + key[-4:]


def redact(text: str, key: Optional[str]) -> str:
    """Replace every occurrence of *key* in *text* with a placeholder."""
    if not key or not text:
        return text
    return text.replace(key, REDACTED)


class RedactingFilter(logging.Filter):
    """Logging filter that masks the API key in messages and formatted args."""

    def __init__(self, key: Optional[str]) -> None:
        super().__init__()
        self._key = key or ""

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._key:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        if self._key in message:
            record.msg = redact(message, self._key)
            record.args = ()
        return True


def install_redaction(key: Optional[str], logger: Optional[logging.Logger] = None) -> None:
    """Attach a :class:`RedactingFilter` to *logger* (root by default) and its handlers."""
    if not key:
        return
    target = logger or logging.getLogger()
    filt = RedactingFilter(key)
    target.addFilter(filt)
    for handler in target.handlers:
        handler.addFilter(filt)


def redacting_writer(key: Optional[str], write: Callable[[str], object]) -> Callable[[str], object]:
    """Wrap a text ``write`` callable so the key can never reach the sink."""
    if not key:
        return write

    def _write(text: str):
        return write(redact(text, key))

    return _write


# ── CLI: manage the key without ever typing it on the command line ────────────


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python meraki_env.py",
        description="Inspect or store the Meraki API key used by this pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "commands:\n"
            "  status   show which source provides the key (env, .env, file, keyring)\n"
            "  store    prompt for the key (hidden input) and save it to the OS keyring\n"
            "\n"
            "The key is never accepted as an argument so it stays out of shell history."
        ),
    )
    parser.add_argument("command", choices=["status", "store"], nargs="?", default="status")
    args = parser.parse_args(argv)

    if args.command == "store":
        import getpass

        value = getpass.getpass("Meraki API key (input hidden): ").strip()
        if not _is_real_key(value):
            print("No key entered; nothing stored.", file=sys.stderr)
            return 1
        if _keyring_set(value):
            print(f"Stored key {mask_key(value)} in OS keyring (service: {KEYRING_SERVICE}).")
            return 0
        print(
            "Could not store the key: install the optional dependency with `pip install keyring` "
            "and make sure a keyring backend is available.",
            file=sys.stderr,
        )
        return 1

    key, source = resolve_api_key()
    if key is None:
        print(MISSING_KEY_HELP, file=sys.stderr)
        return 1
    print(f"Meraki API key: {mask_key(key)} (source: {source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
