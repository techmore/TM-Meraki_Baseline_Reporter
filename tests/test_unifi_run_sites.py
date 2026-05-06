from datetime import datetime, timezone
from pathlib import Path

from unifi.run_sites import build_site_index_html, write_site_index


def test_unifi_site_index_links_profile_reports(tmp_path: Path):
    reports_root = tmp_path / "reports"
    site_dir = reports_root / "First_Campus"
    site_dir.mkdir(parents=True)
    (site_dir / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (site_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    manifest = {
        "ok": True,
        "profiles": [
            {
                "profile": "site1",
                "name": "First Campus",
                "collectionStatus": "ok",
                "reportStatus": "ok",
                "reportsDir": str(site_dir),
            }
        ],
    }

    html = build_site_index_html(manifest, reports_root, datetime(2026, 5, 5, tzinfo=timezone.utc))

    assert "TM UniFi Site Reports" in html
    assert "First Campus" in html
    assert 'href="First_Campus/report.pdf"' in html
    assert 'href="First_Campus/index.html"' in html
    assert "site_run_manifest.json" in html


def test_unifi_site_index_marks_failed_profiles(tmp_path: Path):
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    manifest = {
        "ok": False,
        "profiles": [
            {
                "profile": "site2",
                "name": "Second Campus",
                "collectionStatus": "failed",
                "reportStatus": "missing_backup",
                "reportsDir": str(reports_root / "Second_Campus"),
            }
        ],
    }

    index_path = write_site_index(manifest, reports_root)
    html = index_path.read_text(encoding="utf-8")

    assert index_path == reports_root / "index.html"
    assert "Needs attention" in html
    assert "Second Campus" in html
    assert "failed" in html
    assert "missing_backup" in html
