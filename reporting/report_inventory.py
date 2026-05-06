"""Validate and summarize generated report deliverables."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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


def _has_named_alias(org_dir: Path, pattern: str) -> bool:
    return any(path.is_file() for path in org_dir.glob(pattern))


def inspect_org_dir(org_dir: Path) -> InventoryResult:
    present: list[Deliverable] = []
    missing: list[Deliverable] = []

    for deliverable in EXPECTED_DELIVERABLES:
        compat_path = org_dir / deliverable.compat_name
        if compat_path.is_file() and _has_named_alias(org_dir, deliverable.named_pattern):
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


def print_inventory(results: tuple[InventoryResult, ...]) -> None:
    for result in results:
        print(f"{result.org_dir.name}: {len(result.present)}/{len(EXPECTED_DELIVERABLES)} expected deliverables")
        for deliverable in result.present:
            compat_path = result.org_dir / deliverable.compat_name
            print(f"  OK  {deliverable.label}: {deliverable.compat_name} ({_fmt_size(compat_path)})")
        for deliverable in result.missing:
            print(f"  MISSING  {deliverable.label}: {deliverable.compat_name} and {deliverable.named_pattern}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generated report deliverables.")
    parser.add_argument(
        "--reports-dir",
        default="reports",
        help="Reports directory containing latest/<org>/ outputs. Default: reports",
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
    if any(not result.ok for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
