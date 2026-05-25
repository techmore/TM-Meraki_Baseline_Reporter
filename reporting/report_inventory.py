"""Validate and summarize generated report deliverables."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Deliverable:
    label: str
    compat_name: str
    named_pattern: str


EXPECTED_DELIVERABLES: tuple[Deliverable, ...] = (
    Deliverable("Complete report", "report.pdf", "*_Complete_Report_*.pdf"),
    Deliverable("Executive summary", "report_exec_summary.pdf", "*_Executive_Summary_Report_*.pdf"),
    Deliverable("Backup settings", "report_backup_settings.pdf", "*_Backup_Settings_Report_*.pdf"),
    Deliverable(
        "Battery backup",
        "report_battery_backup.pdf",
        "*_Battery_Backup_Pricing_Calculation_Report_*.pdf",
    ),
    Deliverable("AP spectrum", "report_ap_spectrum.pdf", "*_AP_Spectrum_Report_*.pdf"),
    Deliverable("UPS switch power plan", "ups_switch_power_plan.json", "*_UPS_Switch_Power_Plan_Report_*.json"),
)


@dataclass(frozen=True)
class InventoryResult:
    org_dir: Path
    present: tuple[Deliverable, ...]
    missing: tuple[Deliverable, ...]

    @property
    def ok(self) -> bool:
        return not self.missing


def _find_named_alias(org_dir: Path, pattern: str) -> Path | None:
    matches = sorted(path for path in org_dir.glob(pattern) if path.is_file())
    return matches[-1] if matches else None


def inspect_org_dir(org_dir: Path) -> InventoryResult:
    present: list[Deliverable] = []
    missing: list[Deliverable] = []

    for deliverable in EXPECTED_DELIVERABLES:
        compat_path = org_dir / deliverable.compat_name
        if compat_path.is_file() and _find_named_alias(org_dir, deliverable.named_pattern):
            present.append(deliverable)
        else:
            missing.append(deliverable)

    return InventoryResult(org_dir=org_dir, present=tuple(present), missing=tuple(missing))


def inspect_reports_dir(reports_dir: Path) -> tuple[InventoryResult, ...]:
    latest_dir = reports_dir / "latest"
    if not latest_dir.is_dir():
        return ()

    org_dirs = sorted(path for path in latest_dir.iterdir() if path.is_dir() and not path.name.startswith("."))
    return tuple(inspect_org_dir(org_dir) for org_dir in org_dirs)


def _fmt_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "unknown size"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _size_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def print_inventory(results: tuple[InventoryResult, ...]) -> None:
    for result in results:
        print(f"{result.org_dir.name}: {len(result.present)}/{len(EXPECTED_DELIVERABLES)} expected deliverables")
        for deliverable in result.present:
            compat_path = result.org_dir / deliverable.compat_name
            print(f"  OK  {deliverable.label}: {deliverable.compat_name} ({_fmt_size(compat_path)})")
        for deliverable in result.missing:
            print(f"  MISSING  {deliverable.label}: {deliverable.compat_name} and {deliverable.named_pattern}")


def build_manifest(results: tuple[InventoryResult, ...], reports_dir: Path) -> dict:
    latest_dir = reports_dir / "latest"
    orgs = []
    for result in results:
        deliverables = []
        for deliverable in EXPECTED_DELIVERABLES:
            compat_path = result.org_dir / deliverable.compat_name
            named_path = _find_named_alias(result.org_dir, deliverable.named_pattern)
            present = compat_path.is_file() and named_path is not None
            deliverables.append(
                {
                    "label": deliverable.label,
                    "present": present,
                    "compatName": deliverable.compat_name,
                    "compatPath": str(compat_path) if compat_path.exists() else None,
                    "compatSizeBytes": _size_bytes(compat_path) if compat_path.exists() else None,
                    "namedPattern": deliverable.named_pattern,
                    "namedPath": str(named_path) if named_path else None,
                    "namedSizeBytes": _size_bytes(named_path) if named_path else None,
                }
            )
        orgs.append(
            {
                "org": result.org_dir.name,
                "latestPath": str(result.org_dir),
                "status": "ok" if result.ok else "missing",
                "presentCount": len(result.present),
                "expectedCount": len(EXPECTED_DELIVERABLES),
                "deliverables": deliverables,
            }
        )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "reportsDir": str(reports_dir),
        "latestDir": str(latest_dir),
        "status": "ok" if all(result.ok for result in results) else "missing",
        "orgCount": len(results),
        "expectedDeliverables": [deliverable.label for deliverable in EXPECTED_DELIVERABLES],
        "orgs": orgs,
    }


def write_manifest(results: tuple[InventoryResult, ...], reports_dir: Path, manifest_path: Path | None = None) -> Path:
    target = manifest_path or (reports_dir / "latest" / "report_inventory.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(results, reports_dir)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _relative_href(path: Path, base_dir: Path) -> str:
    try:
        rel = path.relative_to(base_dir)
    except ValueError:
        rel = path
    return html.escape(rel.as_posix(), quote=True)


def build_index_html(results: tuple[InventoryResult, ...], reports_dir: Path, generated_at: datetime | None = None) -> str:
    latest_dir = reports_dir / "latest"
    generated = generated_at or datetime.now(timezone.utc)
    status = "OK" if all(result.ok for result in results) else "Missing deliverables"
    org_sections = []
    for result in results:
        rows = []
        for deliverable in EXPECTED_DELIVERABLES:
            compat_path = result.org_dir / deliverable.compat_name
            named_path = _find_named_alias(result.org_dir, deliverable.named_pattern)
            present = compat_path.is_file() and named_path is not None
            if present:
                href = _relative_href(compat_path, latest_dir)
                link = f'<a href="{href}">{html.escape(deliverable.compat_name)}</a>'
                named = html.escape(named_path.name if named_path else "")
                size = _fmt_size(compat_path)
                state = '<span class="status ok">OK</span>'
            else:
                link = html.escape(deliverable.compat_name)
                named = html.escape(deliverable.named_pattern)
                size = "-"
                state = '<span class="status missing">Missing</span>'
            rows.append(
                "<tr>"
                f"<td>{html.escape(deliverable.label)}</td>"
                f"<td>{state}</td>"
                f"<td>{link}</td>"
                f"<td>{named}</td>"
                f"<td>{html.escape(size)}</td>"
                "</tr>"
            )
        org_sections.append(
            "<section>"
            f"<h2>{html.escape(result.org_dir.name)}</h2>"
            f"<p>{len(result.present)} of {len(EXPECTED_DELIVERABLES)} expected deliverables present.</p>"
            "<table>"
            "<thead><tr><th>Deliverable</th><th>Status</th><th>Latest Alias</th><th>Named File</th><th>Size</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</section>"
        )

    manifest_link = '<a href="report_inventory.json">report_inventory.json</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TM Meraki Report Inventory</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; background: #f7f8fb; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    header {{ margin-bottom: 24px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin: 0 0 4px; font-size: 20px; }}
    p {{ margin: 0 0 14px; color: #526071; }}
    section {{ background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e7ebf2; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ color: #526071; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }}
    a {{ color: #185abc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .status {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }}
    .ok {{ background: #e7f5ec; color: #176a35; }}
    .missing {{ background: #fde8e8; color: #a62121; }}
    .meta {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; color: #526071; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TM Meraki Report Inventory</h1>
      <div class="meta">
        <span>Status: {html.escape(status)}</span>
        <span>Generated: {html.escape(generated.isoformat())}</span>
        <span>Manifest: {manifest_link}</span>
      </div>
    </header>
    {''.join(org_sections)}
  </main>
</body>
</html>
"""


def write_index_html(results: tuple[InventoryResult, ...], reports_dir: Path, index_path: Path | None = None) -> Path:
    target = index_path or (reports_dir / "latest" / "index.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_index_html(results, reports_dir), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generated report deliverables.")
    parser.add_argument(
        "--reports-dir",
        default="reports",
        help="Reports directory containing latest/<org>/ outputs. Default: reports",
    )
    parser.add_argument(
        "--manifest",
        help="Optional manifest path. Default: <reports-dir>/latest/report_inventory.json",
    )
    parser.add_argument(
        "--index",
        help="Optional HTML index path. Default: <reports-dir>/latest/index.html",
    )
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir).resolve()
    latest_dir = reports_dir / "latest"
    if not latest_dir.is_dir():
        print(f"No latest reports directory found: {latest_dir}")
        return 1

    results = inspect_reports_dir(reports_dir)
    if not results:
        print(f"No organization report directories found in {latest_dir}")
        return 1

    print_inventory(results)
    manifest_path = write_manifest(results, reports_dir, Path(args.manifest).resolve() if args.manifest else None)
    print(f"Manifest: {manifest_path}")
    index_path = write_index_html(results, reports_dir, Path(args.index).resolve() if args.index else None)
    print(f"Index: {index_path}")
    if any(not result.ok for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
