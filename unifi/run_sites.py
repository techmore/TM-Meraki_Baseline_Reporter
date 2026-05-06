#!/usr/bin/env python3
import argparse
import html
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List

from . import collect, report
from .profiles import UniFiSiteProfile, discover_site_profiles, profile_by_key


ROOT = Path(__file__).resolve().parents[1]


def _relative_href(path: Path, base: Path) -> str:
    try:
        rel = os.path.relpath(path.resolve(), base.resolve())
    except OSError:
        rel = str(path)
    return html.escape(Path(rel).as_posix(), quote=True)


def _status_badge(value: object) -> str:
    raw = str(value or "unknown")
    css = "ok" if raw == "ok" else ("warn" if raw in {"skipped", "missing_backup"} else "bad")
    return f'<span class="status {css}">{html.escape(raw)}</span>'


def build_site_index_html(manifest: Dict[str, object], reports_root: Path, generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now(timezone.utc)
    profiles = [profile for profile in manifest.get("profiles", []) if isinstance(profile, dict)]
    rows = []
    for profile in profiles:
        reports_dir = Path(str(profile.get("reportsDir") or ""))
        report_pdf = reports_dir / "report.pdf"
        profile_index = reports_dir / "index.html"
        if report_pdf.exists():
            report_link = f'<a href="{_relative_href(report_pdf, reports_root)}">report.pdf</a>'
        else:
            report_link = "report.pdf"
        if profile_index.exists():
            inventory_link = f'<a href="{_relative_href(profile_index, reports_root)}">index.html</a>'
        else:
            inventory_link = "index.html"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(profile.get('name') or profile.get('profile') or ''))}</td>"
            f"<td>{html.escape(str(profile.get('profile') or ''))}</td>"
            f"<td>{_status_badge(profile.get('collectionStatus'))}</td>"
            f"<td>{_status_badge(profile.get('reportStatus'))}</td>"
            f"<td>{report_link}</td>"
            f"<td>{inventory_link}</td>"
            "</tr>"
        )

    status = "OK" if manifest.get("ok") else "Needs attention"
    status_class = "ok" if manifest.get("ok") else "bad"
    manifest_link = '<a href="site_run_manifest.json">site_run_manifest.json</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TM UniFi Site Reports</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; background: #f7f8fb; }}
    main {{ max-width: 1080px; margin: 0 auto; }}
    header {{ margin-bottom: 20px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    p {{ margin: 0 0 14px; color: #526071; }}
    section {{ background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 18px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e7ebf2; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ color: #526071; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }}
    a {{ color: #185abc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .meta {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; color: #526071; }}
    .status {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }}
    .ok {{ background: #e7f5ec; color: #176a35; }}
    .warn {{ background: #fff7db; color: #755600; }}
    .bad {{ background: #fde8e8; color: #a62121; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TM UniFi Site Reports</h1>
      <div class="meta">
        <span>Status: <span class="status {status_class}">{html.escape(status)}</span></span>
        <span>Generated: {html.escape(generated.isoformat())}</span>
        <span>Manifest: {manifest_link}</span>
      </div>
    </header>
    <section>
      <p>Saved UniFi profile report outputs for this run.</p>
      <table>
        <thead><tr><th>Site</th><th>Profile</th><th>Collection</th><th>Report</th><th>PDF</th><th>Inventory</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def write_site_index(manifest: Dict[str, object], reports_root: Path) -> Path:
    target = reports_root / "index.html"
    target.write_text(build_site_index_html(manifest, reports_root), encoding="utf-8")
    return target


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
    index_path = write_site_index(manifest, reports_root)
    print("")
    print(f"Site run manifest: {reports_root / 'site_run_manifest.json'}")
    print(f"Site report index: {index_path}")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
