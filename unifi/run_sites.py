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
from .style import index_css


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _profile_summary_metrics(backups_dir: Path) -> Dict[str, object]:
    summary = _load_json(backups_dir / "collection_summary.json", {})
    if not isinstance(summary, dict):
        return {}
    net = summary.get("networkApplication") if isinstance(summary.get("networkApplication"), dict) else {}
    sm = summary.get("siteManager") if isinstance(summary.get("siteManager"), dict) else {}
    site_summaries = net.get("siteSummaries") if isinstance(net.get("siteSummaries"), list) else []
    aggregate = {
        "sites": len(site_summaries) or _int((net.get("counts") or {}).get("sites") if isinstance(net.get("counts"), dict) else 0),
        "devices": 0,
        "clients": 0,
        "networks": 0,
        "wifi": 0,
        "firewallPolicies": 0,
        "firewallZones": 0,
        "telemetryProbeAvailable": 0,
        "telemetryProbeTotal": 0,
        "endpointErrors": len(sm.get("errors") or []) + len(net.get("errors") or []),
        "unsupportedEndpoints": len(sm.get("unsupportedEndpoints") or []) + len(net.get("unsupportedEndpoints") or []),
    }
    for site in site_summaries:
        if not isinstance(site, dict) or not isinstance(site.get("counts"), dict):
            continue
        counts = site["counts"]
        aggregate["devices"] += _int(counts.get("devices"))
        aggregate["clients"] += _int(counts.get("clients"))
        aggregate["networks"] += _int(counts.get("networks"))
        aggregate["wifi"] += _int(counts.get("wifi"))
        aggregate["firewallPolicies"] += _int(counts.get("firewall_policies"))
        aggregate["firewallZones"] += _int(counts.get("firewall_zones"))
        aggregate["telemetryProbeAvailable"] += _int(counts.get("telemetry_probe_available"))
        aggregate["telemetryProbeTotal"] += _int(counts.get("telemetry_probe_total"))

    info_file = (net.get("files") or {}).get("info") if isinstance(net.get("files"), dict) else ""
    if info_file:
        info = _load_json(backups_dir / str(info_file), {})
        if isinstance(info, dict) and info.get("applicationVersion"):
            aggregate["networkVersion"] = str(info.get("applicationVersion"))
    return aggregate


def _metric_value(profile: Dict[str, object], key: str) -> str:
    metrics = profile.get("summaryMetrics") if isinstance(profile.get("summaryMetrics"), dict) else {}
    value = metrics.get(key)
    return "" if value is None else str(value)


def _config_summary(profile: Dict[str, object]) -> str:
    networks = _metric_value(profile, "networks") or "0"
    wifi = _metric_value(profile, "wifi") or "0"
    policies = _metric_value(profile, "firewallPolicies") or "0"
    return f"{networks} net / {wifi} WiFi / {policies} FW"


def _telemetry_summary(profile: Dict[str, object]) -> str:
    available = _metric_value(profile, "telemetryProbeAvailable")
    total = _metric_value(profile, "telemetryProbeTotal")
    if total:
        return f"{available or '0'} / {total}"
    return ""


def _coverage_summary(profile: Dict[str, object]) -> str:
    errors = _metric_value(profile, "endpointErrors") or "0"
    unsupported = _metric_value(profile, "unsupportedEndpoints") or "0"
    return f"{errors} errors / {unsupported} notes"


def build_site_index_html(manifest: Dict[str, object], reports_root: Path, generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now(timezone.utc)
    profiles = [profile for profile in manifest.get("profiles", []) if isinstance(profile, dict)]
    rows = []
    for profile in profiles:
        reports_dir = Path(str(profile.get("reportsDir") or ""))
        report_pdf = reports_dir / "report.pdf"
        exec_pdf = reports_dir / "report_exec_summary.pdf"
        backup_pdf = reports_dir / "report_backup_settings.pdf"
        profile_index = reports_dir / "index.html"
        if report_pdf.exists():
            report_link = f'<a href="{_relative_href(report_pdf, reports_root)}">report.pdf</a>'
        else:
            report_link = "report.pdf"
        if exec_pdf.exists():
            exec_link = f'<a href="{_relative_href(exec_pdf, reports_root)}">exec</a>'
        else:
            exec_link = "exec"
        if backup_pdf.exists():
            backup_link = f'<a href="{_relative_href(backup_pdf, reports_root)}">backup</a>'
        else:
            backup_link = "backup"
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
            f"<td>{html.escape(_metric_value(profile, 'devices'))}</td>"
            f"<td>{html.escape(_metric_value(profile, 'clients'))}</td>"
            f"<td>{html.escape(_config_summary(profile))}</td>"
            f"<td>{html.escape(_telemetry_summary(profile))}</td>"
            f"<td>{html.escape(_coverage_summary(profile))}</td>"
            f"<td>{report_link}</td>"
            f"<td>{exec_link}</td>"
            f"<td>{backup_link}</td>"
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
{index_css()}
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
        <thead><tr><th>Site</th><th>Profile</th><th>Collection</th><th>Report</th><th>Devices</th><th>Clients</th><th>Config</th><th>Telemetry</th><th>Coverage</th><th>Complete</th><th>Exec</th><th>Backup</th><th>Inventory</th></tr></thead>
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
            result["summaryMetrics"] = _profile_summary_metrics(backups_dir)
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
