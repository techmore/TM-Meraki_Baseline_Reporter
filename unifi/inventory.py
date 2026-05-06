#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate UniFi report outputs.")
    parser.add_argument("--reports-dir", default=str(ROOT / "unifi" / "reports" / "latest"))
    parser.add_argument("--backups-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    args = parser.parse_args()

    reports = Path(args.reports_dir)
    backups = Path(args.backups_dir)
    checks = [
        ("collection_summary", backups / "collection_summary.json", True),
        ("report_html", reports / "report.html", False),
        ("report_pdf", reports / "report.pdf", False),
    ]
    items: List[Dict[str, object]] = []
    failed = False
    for label, path, required in checks:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        ok = exists and size > 0
        if required and not ok:
            failed = True
        items.append({"label": label, "path": str(path), "exists": exists, "size": size, "required": required, "ok": ok})

    manifest = {"items": items, "ok": not failed}
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "report_inventory.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for item in items:
        status = "OK" if item["ok"] else ("MISS" if item["required"] else "optional")
        print(f"{status} {item['label']}: {item['path']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

