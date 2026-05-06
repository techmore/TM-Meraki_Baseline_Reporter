from datetime import datetime, timezone
from pathlib import Path

from unifi.run_sites import _profile_summary_metrics, build_site_index_html, write_site_index


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
                "summaryMetrics": {
                    "devices": 12,
                    "clients": 48,
                    "networks": 3,
                    "wifi": 2,
                    "firewallPolicies": 9,
                    "telemetryProbeAvailable": 1,
                    "telemetryProbeTotal": 4,
                    "endpointErrors": 0,
                    "unsupportedEndpoints": 1,
                },
            }
        ],
    }

    html = build_site_index_html(manifest, reports_root, datetime(2026, 5, 5, tzinfo=timezone.utc))

    assert "TM UniFi Site Reports" in html
    assert "max-width: 1180px" in html
    assert "margin: 16px 0" in html
    assert "First Campus" in html
    assert 'href="First_Campus/report.pdf"' in html
    assert 'href="First_Campus/index.html"' in html
    assert "site_run_manifest.json" in html
    assert ">12<" in html
    assert ">48<" in html
    assert "3 net / 2 WiFi / 9 FW" in html
    assert "1 / 4" in html
    assert "0 errors / 1 notes" in html


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


def test_unifi_profile_summary_metrics_aggregates_collection_summary(tmp_path: Path):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "network_info.json").write_text('{"applicationVersion":"10.3.58"}', encoding="utf-8")
    (backups / "collection_summary.json").write_text(
        """
        {
          "siteManager": {"errors": [{"label": "site_manager_sites"}]},
          "networkApplication": {
            "files": {"info": "network_info.json"},
            "counts": {"sites": 1},
            "errors": [],
            "unsupportedEndpoints": [{"label": "Default:vpn_tunnels"}],
            "siteSummaries": [
              {
                "counts": {
                  "devices": 5,
                  "clients": 33,
                  "networks": 2,
                  "wifi": 1,
                  "firewall_policies": 63,
                  "firewall_zones": 6,
                  "telemetry_probe_available": 0,
                  "telemetry_probe_total": 11
                }
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    metrics = _profile_summary_metrics(backups)

    assert metrics["sites"] == 1
    assert metrics["devices"] == 5
    assert metrics["clients"] == 33
    assert metrics["firewallPolicies"] == 63
    assert metrics["endpointErrors"] == 1
    assert metrics["unsupportedEndpoints"] == 1
    assert metrics["networkVersion"] == "10.3.58"
