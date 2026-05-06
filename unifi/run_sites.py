#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List

from . import collect, report
from .profiles import UniFiSiteProfile, discover_site_profiles, profile_by_key


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _profile_environment(profile: UniFiSiteProfile) -> Iterator[None]:
    updates = profile.env_updates()
    clears = [
        "UNIFI_NETWORK_BASE_URL",
        "UNIFI_BASE_URL",
        "UNIFI_NETWORK_CONSOLE_ID",
        "UNIFI_SITE_MANAGER_API_KEY",
        "UNIFI_API_KEY",
    ]
    previous: Dict[str, str | None] = {key: os.environ.get(key) for key in set(clears) | set(updates)}
    try:
        for key in clears:
            os.environ.pop(key, None)
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_inventory(backups_dir: Path, reports_dir: Path) -> int:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "unifi.inventory",
            "--backups-dir",
            str(backups_dir),
            "--reports-dir",
            str(reports_dir),
        ],
        check=False,
    ).returncode


def _run_one(profile: UniFiSiteProfile, args: argparse.Namespace) -> Dict[str, object]:
    backups_dir = Path(args.backups_dir) / profile.safe_name
    reports_dir = Path(args.reports_dir) / profile.safe_name
    result: Dict[str, object] = {
        "profile": profile.key,
        "name": profile.name,
        "safeName": profile.safe_name,
        "backupsDir": str(backups_dir),
        "reportsDir": str(reports_dir),
        "collectionStatus": "skipped" if args.report_only else "pending",
        "reportStatus": "pending",
    }

    print("")
    print(f"=== UniFi profile: {profile.name} ({profile.key}) ===")
    print(f"Backups: {backups_dir}")
    print(f"Reports: {reports_dir}")

    with _profile_environment(profile):
        collect_status = 0
        if args.report_only:
            print("Collection skipped by --report-only")
            if not (backups_dir / "collection_summary.json").exists():
                result["reportStatus"] = "missing_backup"
                print(f"Missing backup summary: {backups_dir / 'collection_summary.json'}")
                return result
        else:
            collect_args = ["--mode", args.mode, "--site-id", profile.site_id, "--output-dir", str(backups_dir)]
            if profile.console_id and not profile.base_url:
                collect_args.extend(["--console-id", profile.console_id])
            collect_status = collect.main(collect_args)
            result["collectionStatus"] = "ok" if collect_status == 0 else "failed"

        if (backups_dir / "collection_summary.json").exists():
            try:
                paths = report.build_report(str(backups_dir), str(reports_dir))
                if args.pdf_only and paths.get("pdf"):
                    try:
                        Path(str(paths["html"])).unlink()
                    except FileNotFoundError:
                        pass
                inventory_status = _run_inventory(backups_dir, reports_dir)
                result["reportStatus"] = "ok" if inventory_status == 0 else "inventory_failed"
                result["report"] = paths
            except Exception as exc:
                result["reportStatus"] = "failed"
                result["error"] = str(exc)
        else:
            result["reportStatus"] = "missing_backup"

    if collect_status != 0:
        result["failed"] = True
    if result.get("reportStatus") != "ok":
        result["failed"] = True
    return result


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run UniFi collection/reporting for saved site profiles.")
    parser.add_argument("--mode", choices=["network"], default="network")
    parser.add_argument("--profile", default="", help="Run one profile by key/name, for example site1")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--backups-dir", default=str(ROOT / "unifi" / "backups" / "sites"))
    parser.add_argument("--reports-dir", default=str(ROOT / "unifi" / "reports" / "sites"))
    parser.add_argument("--pdf-only", action="store_true")
    args = parser.parse_args(argv)

    profiles = discover_site_profiles()
    if args.profile:
        selected = profile_by_key(profiles, args.profile)
        profiles = [selected] if selected else []
    if not profiles:
        print("No saved UniFi site profiles found. Add UNIFI_SITE1_API_KEY plus UNIFI_SITE1_BASE_URL or UNIFI_SITE1_CONSOLE_ID in unifi/.env.", file=sys.stderr)
        return 1

    results = [_run_one(profile, args) for profile in profiles]
    manifest = {
        "profiles": results,
        "ok": not any(result.get("failed") for result in results),
    }
    reports_root = Path(args.reports_dir)
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / "site_run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("")
    print(f"Site run manifest: {reports_root / 'site_run_manifest.json'}")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

