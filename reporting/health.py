"""Read-only environment health checks for the Meraki reporting pipeline."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from meraki_env import PLACEHOLDER_KEYS, load_env, resolve_api_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = PROJECT_ROOT / "backups"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    required: bool = True


def _env_value_from_file(path: Path, key_name: str) -> str:
    if not path.exists():
        return ""
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == key_name:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def api_key_source() -> str:
    """Return where the API key comes from (env, .env, file, keyring) or "missing"."""
    load_env(str(PROJECT_ROOT / ".env"))
    value = os.environ.get("MERAKI_API_KEY") or _env_value_from_file(PROJECT_ROOT / ".env", "MERAKI_API_KEY")
    if value not in PLACEHOLDER_KEYS:
        return "env"
    _key, source = resolve_api_key(str(PROJECT_ROOT / ".env"))
    return source


def has_meraki_api_key() -> bool:
    return api_key_source() != "missing"


def org_backup_dirs(backups_dir: Path | None = None) -> list[Path]:
    # Resolve at call time: a default of BACKUPS_DIR would be frozen at import and ignore later overrides.
    backups_dir = BACKUPS_DIR if backups_dir is None else backups_dir
    if not backups_dir.exists():
        return []
    return sorted(path for path in backups_dir.iterdir() if path.is_dir() and not path.name.startswith("."))


def module_available(name: str) -> bool:
    # Importing weasyprint installs a progress logger that would otherwise clutter the check output.
    logging.getLogger("weasyprint.progress").setLevel(logging.WARNING)
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


def run_checks(require_api_key: bool = True, require_backups: bool = False, require_ollama: bool = False) -> list[CheckResult]:
    key_source = api_key_source()
    key_present = key_source != "missing"
    checks = [
        CheckResult("Python runtime", "ok", sys.version.split()[0]),
        CheckResult(
            "Pipeline imports",
            "ok" if all(module_available(name) for name in ("meraki_backup", "merge_recommendations", "reporting.app")) else "fail",
            "core modules import",
        ),
        CheckResult(
            "MERAKI_API_KEY",
            "ok" if key_present else ("fail" if require_api_key else "skip"),
            f"present (source: {key_source})"
            if key_present
            else ("missing — see `python meraki_env.py --help`" if require_api_key else "not required for this mode"),
            required=require_api_key,
        ),
        CheckResult(
            "Backup directory",
            "ok" if BACKUPS_DIR.exists() else ("fail" if require_backups else "skip"),
            str(BACKUPS_DIR),
            required=require_backups,
        ),
    ]

    backup_count = len(org_backup_dirs())
    checks.append(
        CheckResult(
            "Org backups",
            "ok" if backup_count else ("fail" if require_backups else "skip"),
            f"{backup_count} org backup(s)" if backup_count else "none found",
            required=require_backups,
        )
    )

    pdf_detail = ""
    if module_available("weasyprint"):
        pdf_detail = "weasyprint"
    elif shutil.which("wkhtmltopdf"):
        pdf_detail = "wkhtmltopdf"
    checks.append(
        CheckResult(
            "PDF renderer",
            "ok" if pdf_detail else "fail",
            pdf_detail or "weasyprint or wkhtmltopdf required",
        )
    )

    ollama_present = shutil.which("ollama") is not None
    checks.append(
        CheckResult(
            "Ollama",
            "ok" if ollama_present else ("fail" if require_ollama else "skip"),
            "installed" if ollama_present else ("missing" if require_ollama else "optional"),
            required=require_ollama,
        )
    )
    return checks


def print_checks(checks: list[CheckResult]) -> None:
    width = max(len(check.name) for check in checks)
    for check in checks:
        print(f"{check.status.upper():<4}  {check.name:<{width}}  {check.detail}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reporting.health",
        description="Read-only environment check: Python, imports, API key, backups, PDF renderer, Ollama.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit status: 0 when every required check passes, 1 otherwise.\n"
            "\n"
            "examples:\n"
            "  python -m reporting.health                  # full pipeline prerequisites\n"
            "  python -m reporting.health --report-only    # only what report generation needs\n"
            "  python -m reporting.health --require-ollama # also fail when Ollama is missing"
        ),
    )
    parser.add_argument("--report-only", action="store_true", help="Require org backups instead of an API key.")
    parser.add_argument("--require-ollama", action="store_true", help="Treat a missing Ollama install as a failure.")
    parser.add_argument("--no-ai-review", action="store_true", help=argparse.SUPPRESS)  # accepted for run.sh parity
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    checks = run_checks(
        require_api_key=not args.report_only,
        require_backups=args.report_only,
        require_ollama=args.require_ollama,
    )
    print_checks(checks)
    return 1 if any(check.status == "fail" and check.required for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
