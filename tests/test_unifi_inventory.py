import json
from pathlib import Path

from unifi import inventory


def test_unifi_inventory_requires_pdf_and_writes_index(tmp_path: Path):
    backups = tmp_path / "backups"
    reports = tmp_path / "reports"
    backups.mkdir()
    reports.mkdir()
    (backups / "collection_summary.json").write_text("{}", encoding="utf-8")
    (reports / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (reports / "report_exec_summary.pdf").write_bytes(b"%PDF-1.4\n")
    (reports / "report_backup_settings.pdf").write_bytes(b"%PDF-1.4\n")

    assert inventory.main(["--backups-dir", str(backups), "--reports-dir", str(reports)]) == 0

    manifest = json.loads((reports / "report_inventory.json").read_text(encoding="utf-8"))
    index = (reports / "index.html").read_text(encoding="utf-8")

    assert manifest["ok"] is True
    assert {item["label"]: item["ok"] for item in manifest["items"]}["report_pdf"] is True
    assert {item["label"]: item["ok"] for item in manifest["items"]}["report_exec_summary_pdf"] is True
    assert {item["label"]: item["ok"] for item in manifest["items"]}["report_backup_settings_pdf"] is True
    assert {item["label"]: item["required"] for item in manifest["items"]}["report_html"] is False
    assert "TM UniFi Report Inventory" in index
    assert "max-width: 1180px" in index
    assert "margin: 16px 0" in index
    assert "report.pdf" in index
    assert "report_exec_summary.pdf" in index
    assert "report_backup_settings.pdf" in index
    assert "collection_summary.json" in index


def test_unifi_inventory_fails_missing_pdf(tmp_path: Path):
    backups = tmp_path / "backups"
    reports = tmp_path / "reports"
    backups.mkdir()
    reports.mkdir()
    (backups / "collection_summary.json").write_text("{}", encoding="utf-8")

    assert inventory.main(["--backups-dir", str(backups), "--reports-dir", str(reports)]) == 1

    manifest = json.loads((reports / "report_inventory.json").read_text(encoding="utf-8"))
    items = {item["label"]: item for item in manifest["items"]}

    assert manifest["ok"] is False
    assert items["report_pdf"]["required"] is True
    assert items["report_pdf"]["ok"] is False
    assert items["report_exec_summary_pdf"]["required"] is True
    assert items["report_exec_summary_pdf"]["ok"] is False
    assert items["report_backup_settings_pdf"]["required"] is True
    assert items["report_backup_settings_pdf"]["ok"] is False
    assert (reports / "index.html").exists()
