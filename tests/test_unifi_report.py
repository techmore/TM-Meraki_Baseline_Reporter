import json
from pathlib import Path

from unifi.report import build_report


def test_unifi_report_renders_inventory_and_network_sections(tmp_path: Path):
    source = tmp_path / "backup"
    site_dir = source / "sites" / "Main"
    site_dir.mkdir(parents=True)
    (source / "collection_summary.json").write_text(
        json.dumps(
            {
                "metadata": {"requestedMode": "network", "effectiveMode": "network", "collectedAt": "2026-05-05T12:00:00"},
                "networkApplication": {"enabled": True, "files": {"site_summaries": "network_site_summaries.json"}, "errors": []},
            }
        ),
        encoding="utf-8",
    )
    (source / "network_site_summaries.json").write_text(
        json.dumps(
            [
                {
                    "id": "site-1",
                    "name": "Main",
                    "files": {
                        "devices": "sites/Main/devices.json",
                        "clients": "sites/Main/clients.json",
                        "networks": "sites/Main/networks.json",
                        "wifi": "sites/Main/wifi.json",
                        "firewall_zones": "sites/Main/firewall_zones.json",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "devices.json").write_text(
        json.dumps(
            [
                {"name": "U7-Pro-1", "model": "U7-Pro", "type": "access point", "state": "ONLINE", "ipAddress": "10.1.1.10"},
                {"name": "USW-48", "model": "USW-Pro-48-PoE", "type": "switch", "state": "ONLINE", "ipAddress": "10.1.1.20"},
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "clients.json").write_text(json.dumps([{"hostname": "client-1", "ipAddress": "10.10.0.50"}]), encoding="utf-8")
    (site_dir / "networks.json").write_text(json.dumps([{"name": "Staff", "vlanId": 100, "subnet": "10.100.0.0/16", "dhcpMode": "server"}]), encoding="utf-8")
    (site_dir / "wifi.json").write_text(json.dumps([{"name": "Staff WiFi", "enabled": True, "security": "WPA3"}]), encoding="utf-8")
    (site_dir / "firewall_zones.json").write_text(json.dumps([{"name": "Internal", "id": "zone-1"}]), encoding="utf-8")

    output = tmp_path / "report"
    paths = build_report(str(source), str(output))

    html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "TM UniFi Baseline" in html
    assert "U7-Pro-1" in html
    assert "USW-48" in html
    assert "Staff WiFi" in html
    assert "Firewall Zones" in html
