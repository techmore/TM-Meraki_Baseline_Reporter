#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

from .env import load_env


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate UniFi reporting environment.")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--backups-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    args = parser.parse_args(argv)

    load_env()
    failures = 0
    print(f"Python: {sys.version.split()[0]}")

    site_manager = bool(os.getenv("UNIFI_SITE_MANAGER_API_KEY") or os.getenv("UNIFI_API_KEY"))
    network = bool(
        (os.getenv("UNIFI_NETWORK_API_KEY") or os.getenv("UNIFI_API_KEY"))
        and (
            os.getenv("UNIFI_NETWORK_BASE_URL")
            or os.getenv("UNIFI_BASE_URL")
            or os.getenv("UNIFI_NETWORK_CONSOLE_ID")
        )
    )
    print(f"Site Manager API config: {'ok' if site_manager else 'missing'}")
    print(f"Network Application API config: {'ok' if network else 'missing'}")

    if args.report_only:
        backup_summary = Path(args.backups_dir) / "collection_summary.json"
        if backup_summary.exists():
            print(f"Existing UniFi backup: ok ({backup_summary})")
        else:
            failures += 1
            print(f"Existing UniFi backup: missing ({backup_summary})")
    elif not site_manager and not network:
        failures += 1
        print("Set either UNIFI_SITE_MANAGER_API_KEY, or UNIFI_NETWORK_API_KEY plus UNIFI_NETWORK_BASE_URL/UNIFI_NETWORK_CONSOLE_ID.")

    try:
        import weasyprint  # noqa: F401

        print("PDF renderer: weasyprint")
    except Exception:
        print("PDF renderer: unavailable; report.html will still be generated")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
