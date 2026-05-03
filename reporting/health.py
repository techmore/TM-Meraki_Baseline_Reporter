"""Read-only environment health checks for the Meraki reporting pipeline."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from meraki_env import load_env


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = PROJECT_ROOT / "backups"
PLACEHOLDER_KEYS = {"", "your_key_here", "REPLACEME"}


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


def has_meraki_api_key() -> bool:
    load_env(str(PROJECT_ROOT / ".env"))
    value = os.environ.get("MERAKI_API_KEY") or _env_value_from_file(PROJECT_ROOT / ".env", "MERAKI_API_KEY")
    return value not in PLACEHOLDER_KEYS


def org_backup_dirs(backups_dir: Path = BACKUPS_DIR) -> list[Path]:
    if not backups_dir.exists():
        return []
    return sorted(path for path in backups_dir.iterdir() if path.is_dir() and not path.name.startswith("."))


def module_available(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


def run_checks(require_api_key: bool = True, require_backups: bool = False, require_ollama: bool = False) -> list[CheckResult]:
    checks = [
        CheckResult("Python runtime", "ok", sys.version.split()[0]),
        CheckResult(
            "Pipeline imports",
            "ok" if all(module_available(name) for name in ("meraki_backup", "merge_recommendations", "reporting.app")) else "fail",
            "core modules import",
        ),
        CheckResult(
            "MERAKI_API_KEY",
            "ok" if has_meraki_api_key() else ("fail" if require_api_key else "skip"),
            "present" if has_meraki_api_key() else ("missing" if require_api_key else "not required for this mode"),
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


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    mode = "full"
    require_ollama = False
    while argv:
        arg = argv.pop(0)
        if arg == "--report-only":
            mode = "report-only"
        elif arg == "--no-ai-review":
            require_ollama = False
        elif arg == "--require-ollama":
            require_ollama = True
        elif arg in {"--help", "-h"}:
            print("Usage: python -m reporting.health [--report-only] [--require-ollama]")
            return 0
        else:
            print(f"Unknown option: {arg}", file=sys.stderr)
            return 2

    checks = run_checks(
        require_api_key=(mode != "report-only"),
        require_backups=(mode == "report-only"),
        require_ollama=require_ollama,
    )
    print_checks(checks)
    return 1 if any(check.status == "fail" and check.required for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
