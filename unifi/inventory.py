#!/usr/bin/env python3
import argparse
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _fmt_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _relative_href(path: Path, base: Path) -> str:
    try:
        rel = os.path.relpath(path.resolve(), base.resolve())
    except OSError:
        rel = str(path)
    return html.escape(Path(rel).as_posix(), quote=True)


def build_manifest(backups: Path, reports: Path) -> Dict[str, object]:
    checks = [
        ("collection_summary", backups / "collection_summary.json", True),
        ("report_pdf", reports / "report.pdf", True),
        ("report_html", reports / "report.html", False),
    ]
    items: List[Dict[str, object]] = []
    failed = False
    for label, path, required in checks:
        exists = path.exists()
        size = _size(path)
        ok = exists and size > 0
        if required and not ok:
            failed = True
        items.append(
            {
                "label": label,
                "path": str(path),
                "exists": exists,
                "size": size,
                "required": required,
                "ok": ok,
            }
        )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "backupsDir": str(backups),
        "reportsDir": str(reports),
        "items": items,
        "ok": not failed,
    }


def write_index(manifest: Dict[str, object], reports: Path) -> Path:
    items = [item for item in manifest.get("items", []) if isinstance(item, dict)]
    rows = []
    for item in items:
        path = Path(str(item.get("path") or ""))
        ok = bool(item.get("ok"))
        exists = bool(item.get("exists"))
        required = bool(item.get("required"))
        status = "OK" if ok else ("Missing" if required else "Optional")
        status_class = "ok" if ok else ("missing" if required else "optional")
        label = html.escape(str(item.get("label") or ""))
        size = _fmt_size(int(item.get("size") or 0)) if exists else "-"
        if exists:
            link = f'<a href="{_relative_href(path, reports)}">{html.escape(path.name)}</a>'
        else:
            link = html.escape(path.name)
        rows.append(
            "<tr>"
            f"<td>{label}</td>"
            f"<td><span class=\"status {status_class}\">{html.escape(status)}</span></td>"
            f"<td>{link}</td>"
            f"<td>{html.escape(size)}</td>"
            "</tr>"
        )

    status_text = "OK" if manifest.get("ok") else "Missing required output"
    generated = html.escape(str(manifest.get("generatedAt") or ""))
    manifest_link = '<a href="report_inventory.json">report_inventory.json</a>'
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TM UniFi Report Inventory</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; background: #f7f8fb; }}
    main {{ max-width: 980px; margin: 0 auto; }}
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
    .missing {{ background: #fde8e8; color: #a62121; }}
    .optional {{ background: #eef2f7; color: #526071; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TM UniFi Report Inventory</h1>
      <div class="meta">
        <span>Status: {html.escape(status_text)}</span>
        <span>Generated: {generated}</span>
        <span>Manifest: {manifest_link}</span>
      </div>
    </header>
    <section>
      <p>Generated UniFi backup and report deliverables for this run.</p>
      <table>
        <thead><tr><th>Deliverable</th><th>Status</th><th>File</th><th>Size</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    target = reports / "index.html"
    target.write_text(body, encoding="utf-8")
    return target


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate UniFi report outputs.")
    parser.add_argument("--reports-dir", default=str(ROOT / "unifi" / "reports" / "latest"))
    parser.add_argument("--backups-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    args = parser.parse_args(argv)

    reports = Path(args.reports_dir)
    backups = Path(args.backups_dir)
    reports.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(backups, reports)
    (reports / "report_inventory.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    index_path = write_index(manifest, reports)
    for item in manifest["items"]:
        status = "OK" if item["ok"] else ("MISS" if item["required"] else "optional")
        print(f"{status} {item['label']}: {item['path']}")
    print(f"Index: {index_path}")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
