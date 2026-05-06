import json
from pathlib import Path

from unifi.client import UniFiRequestError
from unifi.collect import _call_list, _collect_telemetry_probes
from unifi.report import build_report, _network_detail_finding
from unifi.profiles import discover_site_profiles


def test_unifi_report_renders_inventory_and_network_sections(tmp_path: Path):
    source = tmp_path / "backup"
    site_dir = source / "sites" / "Main"
    site_dir.mkdir(parents=True)
    (source / "collection_summary.json").write_text(
        json.dumps(
            {
                "metadata": {"requestedMode": "network", "effectiveMode": "network", "collectedAt": "2026-05-05T12:00:00"},
                "networkApplication": {
                    "enabled": True,
                    "files": {"site_summaries": "network_site_summaries.json", "info": "network_info.json"},
                    "errors": [],
                    "unsupportedEndpoints": [
                        {"label": "Main:vpn_tunnels", "status": 404, "path": "/vpn/tunnels", "note": "Not exposed."}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "network_info.json").write_text(json.dumps({"applicationVersion": "10.3.58"}), encoding="utf-8")
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
                        "firewall_policies": "sites/Main/firewall_policies.json",
                        "dns_policies": "sites/Main/dns_policies.json",
                        "wans": "sites/Main/wans.json",
                        "vpn_servers": "sites/Main/vpn_servers.json",
                        "radius": "sites/Main/radius.json",
                        "hotspot_vouchers": "sites/Main/hotspot_vouchers.json",
                        "vpn_tunnels": "sites/Main/vpn_tunnels.json",
                        "telemetry_probe": "sites/Main/telemetry_probe.json",
                    },
                    "counts": {
                        "devices": 4,
                        "clients": 10,
                        "networks": 1,
                        "wifi": 1,
                        "firewall_zones": 1,
                        "firewall_policies": 1,
                        "dns_policies": 0,
                        "wans": 1,
                        "vpn_servers": 1,
                        "radius": 1,
                        "hotspot_vouchers": 1,
                        "vpn_tunnels": 0,
                        "telemetry_probe_available": 0,
                        "telemetry_probe_total": 3,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "devices.json").write_text(
        json.dumps(
            [
                {"id": "ap-1", "name": "U7-Pro-1", "model": "U7-Pro", "type": "access point", "state": "ONLINE", "ipAddress": "10.1.1.10", "interfaces": ["ports", "radios"], "features": ["accessPoint"]},
                {"name": "IW HD", "model": "IW HD", "features": ["switching", "accessPoint"], "interfaces": ["ports", "radios"], "state": "ONLINE", "ipAddress": "10.1.1.11"},
                {"name": "USW-48", "model": "USW-Pro-48-PoE", "type": "switch", "state": "ONLINE", "ipAddress": "10.1.1.20", "interfaces": ["ports"], "features": ["switching"]},
                {"name": "USW Flex 2.5G 5", "model": "USW Flex 2.5G 5", "type": "switch", "state": "ONLINE", "ipAddress": "10.1.1.21", "interfaces": ["ports"], "features": ["switching"]},
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "clients.json").write_text(
        json.dumps(
            [
                {"hostname": f"client-{i}", "type": "WIRELESS", "ipAddress": f"10.100.0.{50 + i}", "uplinkDeviceId": "ap-1", "access": {"type": "DEFAULT"}}
                for i in range(10)
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "networks.json").write_text(
        json.dumps(
            [
                {
                    "name": "Staff",
                    "vlanId": 100,
                    "subnet": "10.100.0.0/16",
                    "gateway": "10.100.0.1",
                    "dhcpMode": "server",
                    "dhcpRangeStart": "10.100.0.10",
                    "dhcpRangeEnd": "10.100.0.250",
                    "dnsServers": ["10.10.0.5"],
                    "zoneId": "zone-1",
                    "metadata": {"origin": "USER_DEFINED"},
                }
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "wifi.json").write_text(
        json.dumps(
            [
                {
                    "name": "Staff WiFi",
                    "enabled": True,
                    "securityConfiguration": {"type": "WPA3"},
                    "network": {"type": "NATIVE"},
                    "broadcastingFrequenciesGHz": [2.4, 5],
                }
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "firewall_zones.json").write_text(json.dumps([{"name": "Internal", "id": "zone-1"}]), encoding="utf-8")
    (site_dir / "firewall_policies.json").write_text(
        json.dumps(
            [
                {"name": "Allow Staff", "enabled": True, "action": {"type": "ALLOW"}},
                {
                    "name": "Allow mDNS",
                    "enabled": True,
                    "action": {"type": "ALLOW", "allowReturnTraffic": True},
                    "source": {"zoneId": "zone-1", "trafficFilter": {"portFilter": {"items": [{"type": "PORT_NUMBER", "value": 5353}], "type": "PORTS"}}},
                    "destination": {"trafficFilter": {"ipAddressFilter": {"items": [{"type": "SUBNET", "value": "224.0.0.0/24"}], "type": "IP_ADDRESSES"}}},
                    "ipProtocolScope": {"ipVersion": "IPV4_AND_IPV6", "protocolFilter": {"protocol": {"name": "UDP"}, "type": "NAMED_PROTOCOL"}},
                    "loggingEnabled": False,
                    "metadata": {"origin": "USER_DEFINED"},
                },
                {
                    "name": "Allow All Traffic",
                    "enabled": True,
                    "action": {"type": "ALLOW", "allowReturnTraffic": True},
                    "source": {"zoneId": "zone-1"},
                    "destination": {"zoneId": "zone-1"},
                    "loggingEnabled": False,
                    "metadata": {"origin": "SYSTEM_DEFINED"},
                },
            ]
        ),
        encoding="utf-8",
    )
    (site_dir / "dns_policies.json").write_text(json.dumps([]), encoding="utf-8")
    (site_dir / "wans.json").write_text(json.dumps([{"name": "Internet 1", "id": "wan-1", "addressingType": "DHCP", "dnsServers": ["1.1.1.1"]}]), encoding="utf-8")
    (site_dir / "vpn_servers.json").write_text(json.dumps([{"name": "Corp VPN", "enabled": True, "type": "wireguard", "metadata": {"origin": "USER_DEFINED"}}]), encoding="utf-8")
    (site_dir / "radius.json").write_text(json.dumps([{"name": "Default RADIUS", "host": "10.10.0.5", "authPort": 1812, "metadata": {"origin": "USER_DEFINED"}}]), encoding="utf-8")
    (site_dir / "hotspot_vouchers.json").write_text(json.dumps([{"code": "guest-123", "status": "active", "durationMinutes": 60}]), encoding="utf-8")
    (site_dir / "vpn_tunnels.json").write_text(json.dumps([]), encoding="utf-8")
    (site_dir / "telemetry_probe.json").write_text(
        json.dumps(
            [
                {"label": "site_ports", "purpose": "Per-site switch port telemetry", "path": "/ports", "available": False, "status": 404, "itemCount": 0},
                {"label": "wireless_radios", "purpose": "Wireless radio telemetry", "path": "/wireless/radios", "available": False, "status": 404, "itemCount": 0},
                {"label": "device_ports_switch", "purpose": "Per-switch port telemetry", "path": "/devices/switch-1/ports", "available": False, "status": 404, "itemCount": 0, "sampleDevice": "USW-48 (USW-Pro-48-PoE)", "role": "switch"},
            ]
        ),
        encoding="utf-8",
    )

    output = tmp_path / "report"
    paths = build_report(str(source), str(output))

    html = Path(paths["html"]).read_text(encoding="utf-8")
    exec_html = Path(paths["exec_html"]).read_text(encoding="utf-8")
    backup_html = Path(paths["backup_html"]).read_text(encoding="utf-8")
    assert "TM UniFi Baseline" in html
    assert 'class="cover-site">Main</p>' in html
    assert 'href="#1-executive-summary"' in html
    assert 'id="1-executive-summary"' in html
    assert "U7-Pro-1" in html
    assert "IW HD" in html
    assert "USW-48" in html
    assert "Staff WiFi" in html
    assert "WPA3" in html
    assert "NATIVE" in html
    assert "Firewall Zones" in html
    assert "Recommended Follow-Up" in html
    assert "Current State Assessment" in html
    assert "Top Operational Risks" in html
    assert "Recommended Priorities" in html
    assert "Client concentration requires validation" in html
    assert "Validate client concentration" in html
    assert "Client access policy appears flat" in html
    assert "Review UniFi client access policy design" in html
    assert "Data Confidence Snapshot" in html
    assert "Health at a Glance" in html
    assert "How to Use This Report" in html
    assert "Security Baseline" in html
    assert "Client access policy" in html
    assert "Port and radio diagnostics are low-confidence" in html
    assert "Client Analysis" in html
    assert "Client Overview Summary" in html
    assert "Client Concentration by Uplink" in html
    assert "Recommendations &amp; Implementation Plan" in html
    assert "Validate concentrated client load" in html
    assert "Review client access policy segmentation" in html
    assert "Choose a deeper diagnostics source" in html
    assert "Hardware Refresh &amp; Budget Planning" in html
    assert "Refresh Action Summary" in html
    assert "Unpriced refresh candidates" in html
    assert "Retain / monitor" in html
    assert "Not in refresh scope" in html
    assert "Not quoted" in html
    assert "Small UniFi switch or edge form factor" in html
    assert "Model-Level Refresh Planning" in html
    assert "U7 Pro" in html
    assert "Pro 48 PoE" in html
    assert "By Model" in html
    assert "Client Load by Uplink" in html
    assert "Firewall Policy Summary" in html
    assert "Internal" in html
    assert "Port: 5353" in html
    assert "IP: 224.0.0.0/24" in html
    assert "IPv4 and IPv6; UDP" in html
    assert "Broad allow policies" in html
    assert "system-defined" in html
    assert "controller/system defaults" in html
    assert "enabled user-defined broad allow" not in html
    assert "Network Services Backup" in html
    assert "Internet 1" in html
    assert "Corp VPN" in html
    assert "Default RADIUS" in html
    assert "guest-123" in html
    assert "U7-Pro-1 (U7-Pro)" in html
    assert "Firmware" in html
    assert "Network Application version" in html
    assert "10.3.58" in html
    assert "Telemetry Recovery Plan" in html
    assert "API limitation" in html
    assert "Interface Telemetry Coverage" in html
    assert "ports, radios" in html
    assert "capability flag only" in html
    assert "API Telemetry Probe Results" in html
    assert "site_ports" in html
    assert "device_ports_switch" in html
    assert "USW-48 (USW-Pro-48-PoE) [switch]" in html
    assert "HTTP 404" in html
    assert "Configuration Backup Completeness" in html
    assert "Networks / VLANs" in html
    assert "Configured Networks / VLANs" in html
    assert "Observed Client Address Space" in html
    assert "10.100.0.0/16" in html
    assert "10.100.0.10 - 10.100.0.250" in html
    assert "10.100.0.0/24" in html
    assert "Staff (VLAN 100)" in html
    assert "U7-Pro-1 (U7-Pro): 10" in html
    assert "not an authoritative DHCP lease export" in html
    assert "0 / 3 available" in html
    assert "captured empty" in html
    assert "not exposed (HTTP 404)" in html
    assert "UniFi Executive Summary" in exec_html
    assert "Top Operational Risks" in exec_html
    assert "Recommendations &amp; Implementation Plan" in exec_html
    assert "Hardware Refresh &amp; Budget Planning" in exec_html
    assert "Firewall and Policy Backup" not in exec_html
    assert "UniFi Backup Settings Report" in backup_html
    assert "Configuration Backup Completeness" in backup_html
    assert "Telemetry Recovery Plan" in backup_html
    assert "Firewall and Policy Backup" in backup_html
    assert "Network Services Backup" in backup_html
    assert "Hardware Refresh &amp; Budget Planning" not in backup_html
    assert "Connected Clients" not in backup_html
    assert "End of Report" in backup_html


def test_unifi_profiles_discovers_numbered_site_profiles(monkeypatch):
    monkeypatch.setenv("UNIFI_SITE1_NAME", "First Campus")
    monkeypatch.setenv("UNIFI_SITE1_API_KEY", "secret-one")
    monkeypatch.setenv("UNIFI_SITE1_CONSOLE_ID", "console-1")
    monkeypatch.setenv("UNIFI_SITE1_SITE_ID", "default")
    monkeypatch.setenv("UNIFI_SITE2_API_KEY", "secret-two")
    monkeypatch.setenv("UNIFI_SITE2_BASE_URL", "https://10.0.0.1")

    profiles = discover_site_profiles(load_files=False)

    assert [profile.key for profile in profiles] == ["site1", "site2"]
    assert profiles[0].safe_name == "First_Campus"
    assert profiles[0].env_updates()["UNIFI_NETWORK_CONSOLE_ID"] == "console-1"
    assert profiles[1].env_updates()["UNIFI_NETWORK_BASE_URL"] == "https://10.0.0.1"


def test_unifi_report_network_detail_finding_flags_missing_address_fields():
    finding = _network_detail_finding(
        [
            {"name": "Default", "vlanId": 1},
            {"name": "Guest", "vlanId": 100},
        ]
    )

    assert finding is not None
    assert finding["status"] == "Low detail"
    assert "none expose subnet, gateway, DHCP mode, or DHCP range" in finding["summary"]


def test_unifi_report_surfaces_remote_connector_auth_guidance(tmp_path: Path):
    source = tmp_path / "backup"
    source.mkdir()
    (source / "collection_summary.json").write_text(
        json.dumps(
            {
                "metadata": {"requestedMode": "network", "effectiveMode": "network"},
                "networkApplication": {
                    "enabled": True,
                    "connectionType": "remote",
                    "errors": [{"label": "network_sites", "status": 401, "path": "/remote", "error": "unauthorized"}],
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report"
    paths = build_report(str(source), str(output))
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "Credential / Access Fix" in html
    assert "cloud/account API key with console access" in html


def test_unifi_report_surfaces_local_connectivity_guidance(tmp_path: Path):
    source = tmp_path / "backup"
    source.mkdir()
    (source / "collection_summary.json").write_text(
        json.dumps(
            {
                "metadata": {"requestedMode": "network", "effectiveMode": "network"},
                "networkApplication": {
                    "enabled": True,
                    "connectionType": "local",
                    "errors": [{"label": "network_sites", "status": None, "path": "/local", "error": "timed out"}],
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report"
    paths = build_report(str(source), str(output))
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "Credential / Access Fix" in html
    assert "Local UniFi console could not be reached" in html
    assert "UNIFI_NETWORK_BASE_URL" in html


def test_unifi_report_lists_optional_unsupported_endpoints(tmp_path: Path):
    source = tmp_path / "backup"
    source.mkdir()
    (source / "collection_summary.json").write_text(
        json.dumps(
            {
                "metadata": {"requestedMode": "network", "effectiveMode": "network"},
                "networkApplication": {
                    "enabled": True,
                    "errors": [],
                    "unsupportedEndpoints": [
                        {
                            "label": "Default:vpn_tunnels",
                            "status": 404,
                            "path": "/vpn/tunnels",
                            "note": "This UniFi Network version does not expose VPN tunnel listing.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report"
    paths = build_report(str(source), str(output))
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "Optional API Coverage Notes" in html
    assert "Default:vpn_tunnels" in html
    assert "does not expose VPN tunnel listing" in html


def test_unifi_collect_treats_optional_404_as_unsupported():
    class MissingEndpointClient:
        def paged_get(self, path, *, style):
            raise UniFiRequestError("HTTP 404", status=404)

    errors = []
    unsupported = []

    result = _call_list(
        MissingEndpointClient(),
        "/vpn/tunnels",
        style="offset",
        label="Default:vpn_tunnels",
        errors=errors,
        unsupported=unsupported,
        optional_404_note="Not exposed by this controller.",
    )

    assert result == []
    assert errors == []
    assert unsupported[0]["label"] == "Default:vpn_tunnels"
    assert unsupported[0]["note"] == "Not exposed by this controller."


def test_unifi_collect_telemetry_probe_records_available_and_missing_paths(tmp_path: Path):
    class ProbeClient:
        def get_json(self, path, params=None):
            if path.endswith("/sites/site-1/ports"):
                return {"data": [{"port": 1}, {"port": 2}]}
            if path.endswith("/devices/switch-1/ports"):
                return {"data": [{"port": 1}]}
            raise UniFiRequestError("HTTP 404", status=404)

    results = _collect_telemetry_probes(
        ProbeClient(),
        "/network",
        "site-1",
        "Main",
        [
            {"id": "combo-1", "name": "IW HD", "model": "IW HD", "state": "ONLINE", "interfaces": ["ports", "radios"], "features": ["switching", "accessPoint"]},
            {"id": "ap-1", "name": "U7-Pro-1", "model": "U7-Pro", "interfaces": ["ports", "radios"], "features": ["accessPoint"]},
            {"id": "old-switch", "name": "Old Switch", "model": "USW Flex", "state": "OFFLINE", "interfaces": ["ports"], "features": ["switching"]},
            {"id": "switch-1", "name": "USW-48", "model": "USW-Pro-48-PoE", "state": "ONLINE", "interfaces": ["ports"], "features": ["switching"]},
            {"id": "gateway-1", "name": "Gateway", "model": "UCG Ultra", "interfaces": ["ports"], "features": ["switching"]},
        ],
        tmp_path,
    )
    by_label = {result["label"]: result for result in results}

    assert by_label["site_ports"]["available"] is True
    assert by_label["site_ports"]["itemCount"] == 2
    assert (tmp_path / by_label["site_ports"]["file"]).exists()
    assert by_label["site_radios"]["status"] == 404
    assert by_label["device_ports_switch"]["path"].endswith("/devices/switch-1/ports")
    assert by_label["device_ports_switch"]["available"] is True
    assert by_label["device_ports_switch"]["sampleDevice"] == "USW-48 (USW-Pro-48-PoE)"
    assert by_label["device_ports_gateway"]["path"].endswith("/devices/gateway-1/ports")
    assert by_label["device_ports_ap"]["path"].endswith("/devices/ap-1/ports")
    assert by_label["device_radios_ap"]["path"].endswith("/devices/ap-1/radios")
