"""Tests for reporting/app.py — build_org_report() with fixture data."""
import json
import os
import shutil
import sys
from datetime import datetime
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class TestBuildOrgReport:
    """Smoke tests: build_org_report() on fixture data must produce valid HTML."""

    @pytest.fixture(scope="class")
    def report_html(self):
        from reporting.app import build_org_report
        return build_org_report(FIXTURES, "Test Org")

    def test_produces_string(self, report_html):
        assert isinstance(report_html, str)
        assert len(report_html) > 1000

    def test_no_unclosed_section_tags(self, report_html):
        opens  = report_html.count("<section")
        closes = report_html.count("</section>")
        assert opens == closes, f"Mismatched <section> tags: {opens} open vs {closes} close"

    def test_exec_summary_present(self, report_html):
        assert "executive-summary" in report_html

    def test_toc_entries_link_to_report_sections(self, report_html):
        assert 'class="toc-link" href="#executive-summary"' in report_html
        assert 'class="toc-link" href="#network-overview"' in report_html
        assert 'class="toc-link" href="#config-coverage"' in report_html
        assert 'class="toc-link" href="#ups-runtime"' in report_html
        assert 'class="toc-link" href="#switch-deep-dive"' in report_html
        assert 'class="toc-link" href="#unifi-comparison"' in report_html
        assert 'class="toc-link" href="#vlan-reference"' in report_html

    def test_toc_css_uses_denser_spacing(self, report_html):
        from reporting.html_shell import build_html
        html = build_html("Fixture", report_html)
        assert "padding: 44px 64px;" in html
        assert "padding: 6px 0;" in html
        assert ".toc-link" in html

    def test_report_shell_has_header_footer_and_page_numbers(self, report_html):
        from reporting.html_shell import build_html
        html = build_html("Fixture", report_html)
        assert 'content: "TM Meraki Baseline";' in html
        assert 'content: "Release 2026_5_3";' in html
        assert 'content: "Page " counter(page) " of " counter(pages);' in html

    def test_release_and_end_report_page_rendered(self, report_html):
        assert "v2026_5_3" in report_html
        assert "End of Report" in report_html
        assert "TM Meraki Baseline" in report_html

    def test_current_state_assessment_present(self, report_html):
        assert "Current State Assessment" in report_html

    def test_top_operational_risks_present(self, report_html):
        assert "Top Operational Risks" in report_html

    def test_recommended_priorities_present(self, report_html):
        assert "Recommended Priorities" in report_html

    def test_health_grid_present(self, report_html):
        assert "health-grid" in report_html

    def test_exec_health_counts_security_status_case_insensitively(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "security_baseline.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {"check": "AMP", "status": "Pass"},
                        {"check": "IPS", "status": "Warning"},
                        {"check": "Port Forwarding", "status": "Pass"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Security Count Test", report_kind="exec")
        assert "1 warn" in html
        assert "2 checks passed" in html
        assert "0 checks passed" not in html

    def test_exec_health_counts_nested_mx_uplinks(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "uplink_statuses.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2XX-TEST-0001",
                        "model": "MX95",
                        "networkId": "N_test_001",
                        "uplinks": [
                            {"interface": "wan1", "status": "active"},
                            {"interface": "wan2", "status": "ready"},
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "WAN Count Test", report_kind="exec")
        assert "1 active" in html
        assert "1 standby-ready" in html
        assert "No WAN data" not in html

    def test_security_section_present(self, report_html):
        assert "security-baseline" in report_html
        assert "Security Posture Summary" in report_html

    def test_traffic_flows_present(self, report_html):
        assert "traffic-flows" in report_html

    def test_licensing_section_present(self, report_html):
        assert "licensing" in report_html
        assert "Licensing Status" in report_html

    def test_no_python_exceptions_in_output(self, report_html):
        """Report should not contain Python traceback artifacts."""
        assert "Traceback" not in report_html
        assert "AttributeError" not in report_html
        assert "KeyError" not in report_html

    def test_org_name_appears_in_report(self, report_html):
        assert "Test Org" in report_html

    def test_xss_safe_org_name(self):
        from reporting.app import build_org_report
        html = build_org_report(FIXTURES, '<script>alert("xss")</script>')
        assert '<script>alert("xss")</script>' not in html

    def test_security_fail_check_rendered(self, report_html):
        """The fixture has 1 failing IPS check — it should appear in report."""
        assert "Intrusion Prevention" in report_html

    def test_licensing_expired_rendered(self, report_html):
        """Fixture has one expired license key."""
        assert "Expired" in report_html or "expired" in report_html.lower()

    def test_wpc_topology_excluded(self, report_html):
        """Topology section should still exist even with empty LLDP fixture."""
        assert "topology" in report_html

    def test_sparse_data_sections_explain_missing_inputs(self, report_html):
        assert "network_clients.json" in report_html
        assert "Port telemetry available:" in report_html
        assert "LLDP/CDP neighbor data available:" in report_html

    def test_switch_identity_and_poe_budget_reference_render(self, report_html):
        assert "Switch Identity &amp; PoE Budget Reference" in report_html
        assert "Core-SW-1" in report_html
        assert "Q2SW-TEST-0001" in report_html
        assert "MS225-24P" in report_html
        assert "370 W" in report_html
        assert "MS225 Overview and Specifications" in report_html

    def test_poe_analysis_uses_catalog_budget_and_switch_labels(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "poe_power_summary.json").write_text(
            json.dumps(
                {
                    "switch_poe_totals": [
                        {
                            "serial": "Q2SW-TEST-0001",
                            "avgWatts": 42.5,
                            "powerUsageInWh": 1020,
                        }
                    ],
                    "port_poe_totals": [],
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "PoE Test")
        assert "PoE Budget Reference Coverage" in html
        assert "they do not yet include authoritative switch maximum PoE budget values" not in html
        assert "<th>Known Budget</th>" in html
        assert "<th>Headroom</th>" in html
        assert "Core-SW-1 (Q2SW-TEST-0001)" in html
        assert "327.5 W" in html

    def test_ups_runtime_planning_uses_poe_and_apc_reference(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "poe_power_summary.json").write_text(
            json.dumps(
                {
                    "switch_poe_totals": [
                        {
                            "serial": "Q2SW-TEST-0001",
                            "avgWatts": 42.5,
                            "powerUsageInWh": 1020,
                        }
                    ],
                    "port_poe_totals": [],
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "UPS Test")
        assert "Battery Backup Runtime Planning" in html
        assert "UPS Runtime Estimate by Switch" in html
        assert "BX1500M ETA" in html
        assert "SMX2200RMLV2U" in html
        assert "Core-SW-1 (Q2SW-TEST-0001)" in html
        assert "97.5 W" in html
        assert "107.3 W" in html
        assert "10% planning buffer" in html
        assert "ups_switch_power_plan.json" in html
        assert "1 UPS + 1 external battery module" in html
        assert "Executive Recommendation" in html
        assert "Use the Smart-UPS X stack as the planning standard" in html

        battery_html = build_org_report(str(tmp_path), "UPS Test", report_kind="battery_backup")
        assert "Battery Backup Runtime Planning" in battery_html
        assert "UPS Runtime Estimate by Switch" in battery_html
        assert "Core-SW-1 (Q2SW-TEST-0001)" in battery_html
        assert "97.5 W" in battery_html
        assert "Executive Recommendation" in battery_html
        assert "Executive Summary" not in battery_html
        assert "$3,487.04" in html

    def test_ups_power_plan_json_payload_includes_buffered_switch_load(self, tmp_path):
        from reporting.app import _load_ups_power_plan_from_org

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "poe_power_summary.json").write_text(
            json.dumps(
                {
                    "switch_poe_totals": [
                        {
                            "serial": "Q2SW-TEST-0001",
                            "avgWatts": 42.5,
                            "powerUsageInWh": 1020,
                        }
                    ],
                    "port_poe_totals": [],
                }
            ),
            encoding="utf-8",
        )

        payload = _load_ups_power_plan_from_org(
            str(tmp_path),
            "UPS Json Test",
            datetime.fromisoformat("2026-05-05T12:00:00"),
        )
        core = next(item for item in payload["switches"] if item["serial"] == "Q2SW-TEST-0001")
        assert payload["planningAssumptions"]["loadBufferPercent"] == 10
        assert core["switchName"] == "Core-SW-1"
        assert core["baseModeledLoadWatts"] == 97.5
        assert core["bufferWatts"] == 9.8
        assert core["sizingLoadWatts"] == 107.3
        assert core["runtimeEstimates"]["SMX2200RMLV2UTargetStack"]["externalBatteryCount"] == 1

    def test_expanded_hardware_catalog_renders_catalyst_poe_budget(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "devices_availabilities.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "CAT-1",
                        "name": "Catalyst Core",
                        "model": "C9300-48UXM",
                        "productType": "switch",
                        "status": "online",
                        "network": {"id": "N_test_001", "name": "Main"},
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Catalyst Test")
        assert "Catalyst Core" in html
        assert "490 W" in html
        assert "Catalyst 9300-M Datasheet" in html

    def test_device_availability_records_are_enriched_from_inventory(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "devices_availabilities.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2AP-TEST-0001",
                        "status": "online",
                        "productType": "wireless",
                        "network": {"id": "N_test_001", "name": "Main"},
                    }
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "inventory_devices.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2AP-TEST-0001",
                        "name": "Library AP",
                        "model": "MR46",
                        "productType": "wireless",
                        "networkId": "N_test_001",
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Inventory Merge Test")
        assert "Library AP" in html
        assert "MR46" in html
        assert "Q2AP-TEST-0001</td><td>Unknown model" not in html

    def test_k12_vlan_reference_renders_as_supplemental_guidance(self, report_html):
        assert "K-12 VLAN Segmentation Reference" in report_html
        assert "Teacher / Classroom Blocks" in report_html
        assert "10.250.0.0/16" in report_html
        assert "target architecture, not evidence of current compliance" in report_html

    def test_unifi_comparison_requires_pricing_reference(self, report_html):
        assert "Pricing needed" in report_html
        assert "$X" not in report_html
        assert "$29/mo" not in report_html

    def test_unifi_comparison_labels_used_meraki_reference_pricing(self, report_html):
        assert "Cisco/Meraki Used-Market Reference" in report_html
        assert "NetworkTigers (used)" in report_html
        assert "$239.99" in report_html
        assert "Used-market" in report_html

    def test_unifi_comparison_breaks_out_migration_cost_confidence(self, report_html):
        assert "Migration Cost Breakdown" in report_html
        assert "Wireless AP hardware" in report_html
        assert "Optics/transceivers" in report_html
        assert "Public MSRP" in report_html

    def test_executive_summary_includes_data_confidence_snapshot(self, report_html):
        assert "Data Confidence Snapshot" in report_html
        assert "Client attachment detail" in report_html
        assert "Firewall and filtering backup" in report_html

    def test_unifi_comparison_uses_org_local_pricing_json(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "inventory_summary.json").write_text(
            json.dumps({"top_models": [["MS225-24P", 2]], "by_type": {"switch": 2}}),
            encoding="utf-8",
        )
        (tmp_path / "pricing.json").write_text(
            json.dumps(
                {
                    "unifi_equivalents": {"MS225": "USW Pro 24 PoE"},
                    "models": {
                        "MS225": {
                            "meraki_unit_cost": 1000,
                            "unifi_unit_cost": 600,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Pricing Test")
        assert "USW Pro 24 PoE" in html
        assert "$2,000" in html
        assert "$1,200" in html
        assert "$800 (40% lower)" in html

    def test_disabled_unconfigured_ssids_are_collapsed(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "wireless_ssids.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {"name": "Staff", "enabled": True, "authMode": "psk"},
                        {"name": "Unconfigured SSID 2", "number": 1, "enabled": False, "authMode": "open"},
                        {"name": "Unconfigured SSID 3", "number": 2, "enabled": False, "authMode": "open"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "SSID Test")
        assert "Staff" in html
        assert "Unconfigured SSID 2" not in html
        assert "2 disabled default/unconfigured SSID slot(s) hidden." in html

    def test_switch_deep_dive_table_css_is_extra_dense(self, report_html):
        from reporting.html_shell import build_html
        html = build_html("Fixture", report_html)
        assert "@page switch-detail" in html
        assert "size: A4 landscape;" in html
        assert "page: switch-detail;" in html
        assert "table.data.switch-detail-table {" in html
        assert "font-size: 4.2px;" in html
        assert "padding: 0.2px 0.6px;" in html
        assert "line-height: 0.95;" in html
        assert "white-space: nowrap;" in html
        assert "margin-left: 0;" in html
        assert ".switch-detail-table .c-neighbor { width: 28%; }" in html

    def test_switch_deep_dive_uses_compact_column_labels(self, report_html):
        assert "<th>Label</th><th>Heat</th><th>Role</th><th>Stat</th><th>Spd</th><th>Dup</th><th>VLAN</th>" in report_html
        assert "<th>Data</th><th>Kbps</th><th>Pwr</th><th>Flg</th><th>Neighbor</th>" in report_html
        assert "Current Throughput" not in report_html
        assert "Connected Device" not in report_html

    def test_client_overview_renders_when_wireless_clients_missing(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "wireless_clients.json").write_text("{}", encoding="utf-8")
        (tmp_path / "clients_overview.json").write_text(
            json.dumps(
                {
                    "N_test_001": {
                        "counts": {"total": 42, "withHeavyUsage": 3},
                        "usages": {"average": 2048, "withHeavyUsageAverage": 5345},
                    }
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Client Test")
        assert "Client Overview Summary" in html
        assert "Total clients" in html
        assert ">42<" in html
        assert "Heavy-usage clients" in html

    def test_network_clients_render_wired_and_wireless_detail(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "network_clients.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {
                            "description": "Teacher Laptop",
                            "mac": "00:11:22:33:44:55",
                            "recentDeviceConnection": "Wireless",
                            "recentDeviceName": "Library AP",
                            "ssid": "Faculty",
                            "vlan": "110",
                            "os": "macOS",
                            "usage": {"sent": 2000, "recv": 3000},
                        },
                        {
                            "description": "Office Printer",
                            "mac": "00:11:22:33:44:66",
                            "recentDeviceConnection": "Wired",
                            "recentDeviceName": "Core-SW-1",
                            "switchport": "5",
                            "vlan": "100",
                            "deviceTypePrediction": "Printer",
                            "usage": {"sent": 100, "recv": 200},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_clients.json").write_text("{}", encoding="utf-8")

        html = build_org_report(str(tmp_path), "Client Detail Test")
        assert "Client detail source: <code>network_clients.json</code>" in html
        assert "Clients by Connection Type" in html
        assert "Wireless" in html
        assert "Wired" in html
        assert "Teacher Laptop" in html
        assert "Office Printer" in html
        assert "Core-SW-1" in html

    def test_addressing_and_dhcp_audit_renders_vlans_and_scope_utilization(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "appliance_vlans.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {
                            "id": "110",
                            "name": "Faculty",
                            "subnet": "10.110.0.0/16",
                            "applianceIp": "10.110.0.1",
                            "dhcpHandling": "Run a DHCP server",
                            "dhcpLeaseTime": "1 day",
                            "dhcpRelayServerIps": [],
                        },
                        {
                            "id": "20",
                            "name": "Facilities",
                            "subnet": "10.20.0.0/16",
                            "applianceIp": "10.20.0.1",
                            "dhcpHandling": "Relay DHCP to another server",
                            "dhcpRelayServerIps": ["10.10.0.5"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "appliance_dhcp_subnets.json").write_text(
            json.dumps(
                {
                    "Q2MX-TEST-0001": [
                        {
                            "vlanId": 110,
                            "subnet": "10.110.0.0/16",
                            "usedCount": 900,
                            "freeCount": 100,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "network_clients.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {"description": "Teacher Laptop", "vlan": "110", "ip": "10.110.4.15"},
                        {"description": "Teacher iPad", "vlan": "110", "ip": "10.110.4.16"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Addressing Test")
        assert "Addressing &amp; DHCP Scope Audit" in html
        assert "10.110.0.0/16" in html
        assert "Run a DHCP server" in html
        assert "Relay DHCP to another server" in html
        assert "10.10.0.5" in html
        assert "90.0% used" in html
        assert "<td>2</td>" in html

    def test_appliance_policy_backup_renders_firewall_and_content_filtering(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "appliance_policy_backup.json").write_text(
            json.dumps(
                {
                    "N_test_001": {
                        "l3FirewallRules": {
                            "rules": [
                                {
                                    "comment": "Deny students to servers",
                                    "policy": "deny",
                                    "protocol": "tcp",
                                    "srcCidr": "10.250.0.0/16",
                                    "destCidr": "10.10.0.0/16",
                                    "destPort": "445",
                                }
                            ],
                            "syslogDefaultRule": True,
                        },
                        "l7FirewallRules": {
                            "rules": [
                                {"policy": "deny", "type": "host", "value": "example.com"}
                            ]
                        },
                        "inboundFirewallRules": {"rules": []},
                        "portForwardingRules": {
                            "rules": [
                                {
                                    "name": "Camera NVR",
                                    "protocol": "tcp",
                                    "publicPort": "8443",
                                    "localPort": "443",
                                    "lanIp": "10.10.0.20",
                                }
                            ]
                        },
                        "contentFiltering": {
                            "blockedUrlCategories": [{"id": "meraki:contentFiltering/category/1", "name": "Adult"}],
                            "allowedUrlPatterns": ["school.edu"],
                            "blockedUrlPatterns": ["bad.example"],
                        },
                        "groupPolicies": [{"groupPolicyId": "101", "name": "Students"}],
                        "siteToSiteVpn": {"mode": "spoke"},
                        "syslogServers": {"servers": [{"host": "10.10.0.50", "port": 514}]},
                    }
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Policy Backup Test")
        assert "MX Firewall, Filtering &amp; Policy Backup" in html
        assert "Deny students to servers" in html
        assert "10.250.0.0/16" in html
        assert "Camera NVR" in html
        assert "Adult" in html
        assert "1 cat / 1 allow / 1 block" in html
        assert "spoke" in html

    def test_firmware_status_renders_current_and_available_versions(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "firmware_upgrades.json").write_text(
            json.dumps(
                [
                    {
                        "network": {"id": "N_test_001", "name": "Main"},
                        "products": {"wireless": True},
                        "currentVersion": {"shortName": "MR 30.6"},
                        "availableVersions": [
                            {"shortName": "MR 31.1", "releaseType": "stable"},
                        ],
                        "isUpgradeAvailable": True,
                        "upgradeStrategy": "minimizeUpgradeTime",
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Firmware Test")
        assert "Firmware Status &amp; Available Versions" in html
        assert "MR 30.6" in html
        assert "MR 31.1" in html
        assert "Upgrade Available" in html

    def test_firmware_status_skips_history_only_rows(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "firmware_upgrades.json").write_text(
            json.dumps(
                [
                    {
                        "network": {"id": "N_test_001", "name": "Main"},
                        "fromVersion": {"shortName": "MR 30.6"},
                        "toVersion": {"shortName": "MR 31.1"},
                        "productTypes": ["wireless"],
                        "status": "Completed",
                        "completedAt": "2026-03-21 01:00:00 UTC",
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Firmware History Test")
        assert "Firmware Status &amp; Available Versions" not in html
        assert "Recent Firmware Upgrades" in html
        assert "MR 31.1" in html

    def test_eos_inventory_highlights_announced_and_two_year_dates(self, monkeypatch, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        monkeypatch.setenv("MERAKI_REPORT_FIXED_NOW", "2026-05-03T12:00:00")
        (tmp_path / "inventory_devices.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "SW-RED",
                        "name": "EOS Soon",
                        "model": "MS220",
                        "networkId": "N_test_001",
                        "eox": {"status": "announced", "endOfSupportAt": "2027-05-03T00:00:00Z"},
                    },
                    {
                        "serial": "SW-YELLOW",
                        "name": "EOS Later",
                        "model": "MS225",
                        "networkId": "N_test_001",
                        "eox": {"status": "announced", "endOfSupportAt": "2029-05-03T00:00:00Z"},
                    },
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "EOS Test")
        assert 'class="row-eos-critical"' in html
        assert 'class="row-eos-announced"' in html

    def test_exec_summary_report_variant(self):
        from reporting.app import build_org_report
        html = build_org_report(FIXTURES, "Test Org", report_kind="exec")
        assert "Executive Summary" in html
        assert "Network Overview" not in html

    def test_backup_settings_report_variant(self):
        from reporting.app import build_org_report
        html = build_org_report(FIXTURES, "Test Org", report_kind="backup")
        assert "Network Overview" in html
        assert "Executive Summary" not in html

    def test_ap_spectrum_report_variant_renders_one_page_per_ap(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        devices = json.loads((tmp_path / "devices_availabilities.json").read_text(encoding="utf-8"))
        devices.append(
            {
                "serial": "Q2AP-TEST-0003",
                "name": "AP-1F-03",
                "productType": "wireless",
                "model": "MR46",
                "status": "online",
                "networkId": "N_test_001",
            }
        )
        (tmp_path / "devices_availabilities.json").write_text(json.dumps(devices), encoding="utf-8")

        (tmp_path / "channel_utilization_by_device.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2AP-TEST-0001",
                        "network": {"id": "N_test_001"},
                        "byBand": [
                            {
                                "band": "5",
                                "wifi": {"percentage": 62},
                                "nonWifi": {"percentage": 2},
                                "total": {"percentage": 78},
                            }
                        ],
                    },
                    {
                        "serial": "Q2AP-TEST-0002",
                        "network": {"id": "N_test_001"},
                        "byBand": [
                            {
                                "band": "5",
                                "wifi": {"percentage": 44},
                                "nonWifi": {"percentage": 1},
                                "total": {"percentage": 61},
                            }
                        ],
                    },
                    {
                        "serial": "Q2AP-TEST-0003",
                        "network": {"id": "N_test_001"},
                        "byBand": [],
                    },
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_rf_profiles.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {
                            "id": "rf-low",
                            "name": "Classroom Low Power",
                            "fiveGhzSettings": {"minPower": 8, "maxPower": 17},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_rf_profile_assignments.json").write_text(
            json.dumps(
                [
                    {
                        "items": [
                            {
                                "network": {"id": "N_test_001"},
                                "name": "AP-1F-01",
                                "serial": "Q2AP-TEST-0001",
                                "model": "MR46",
                                "rfProfile": {
                                    "id": "rf-low",
                                    "name": "Classroom Low Power",
                                    "isIndoorDefault": False,
                                    "isOutdoorDefault": False,
                                },
                            }
                        ]
                    }
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_event_log.json").write_text(
            json.dumps(
                {
                    "N_test_001": {
                        "events": [
                            {
                                "occurredAt": "2026-05-05T12:00:00Z",
                                "type": "association_fail",
                                "description": "802.11 association failure",
                                "category": "80211",
                                "deviceSerial": "Q2AP-TEST-0001",
                                "deviceName": "AP-1F-01",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "AP Spectrum Test", report_kind="ap_spectrum")
        assert "AP Spectrum Availability &amp; Interference Report" in html
        assert html.count("ap-unit-page") >= 2
        assert "Executive Summary / Recommended Action" in html
        assert "Meraki Standards Basis" in html
        assert "RF Telemetry Gaps" in html
        assert "Online Missing RF" in html
        assert "Online but no channel samples were returned" in html
        assert "High Density Wi-Fi Deployments" in html
        assert "Wireless Event Log Context" in html
        assert "association_fail" in html
        assert "WAY TOO CLOSE / saturated RF bubble" in html
        assert "Same-Band Context / Overlap Candidates" in html
        assert "Current RF profile: Classroom Low Power (exact AP assignment)" in html
        assert "remove, disable, or relocate one AP" in html
        assert "Interference Severity Queue" in html
        assert "RF / Hardware Fit" in html
        assert "Wi-Fi 6 / 802.11ax / 2.4, 5 GHz" in html
        assert "Current severe interference means the organization may not feel the value of this Wi-Fi 6 AP until RF is remediated" in html
        assert "Network Overview" not in html

    def test_ap_spectrum_surfaces_channel_utilization_collection_error(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "channel_utilization_by_device.json").write_text(
            json.dumps({"error": "HTTP 400: networkIds must be an array"}),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "AP Spectrum Test", report_kind="ap_spectrum")
        assert "Telemetry Collection Warning" in html
        assert "Channel utilization collection failed for this backup" in html
        assert "networkIds must be an array" in html

    def test_ap_spectrum_distinguishes_external_noise_from_ap_overlap(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        devices = json.loads((tmp_path / "devices_availabilities.json").read_text(encoding="utf-8"))
        for device in devices:
            if device.get("serial") == "Q2AP-TEST-0001":
                device["model"] = "CW9176I"
        (tmp_path / "devices_availabilities.json").write_text(json.dumps(devices), encoding="utf-8")

        (tmp_path / "channel_utilization_by_device.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2AP-TEST-0001",
                        "network": {"id": "N_test_001"},
                        "byBand": [
                            {
                                "band": "5",
                                "wifi": {"percentage": 15},
                                "nonWifi": {"percentage": 82},
                                "total": {"percentage": 97},
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_rf_profiles.json").write_text(
            json.dumps(
                {
                    "N_test_001": [
                        {
                            "id": "rf-stage",
                            "name": "Auditorium",
                            "apBandSettings": {
                                "bands": {"enabled": ["2.4", "5"]},
                            },
                            "fiveGhzSettings": {
                                "minPower": 8,
                                "maxPower": 14,
                                "minBitrate": 24,
                                "channelWidth": "auto",
                                "validAutoChannels": [36, 40, 44, 48],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "wireless_rf_profile_assignments.json").write_text(
            json.dumps(
                [
                    {
                        "serial": "Q2AP-TEST-0001",
                        "rfProfile": {"id": "rf-stage", "name": "Auditorium"},
                    }
                ]
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "AP Spectrum Noise Test", report_kind="ap_spectrum")
        assert "External RF saturation / investigate noise" in html
        assert "Interference Severity Queue" in html
        assert "Critical" in html
        assert "Wi-Fi 7 / 802.11be / 2.4, 5, 6 GHz" in html
        assert "6 GHz capable AP, but this RF profile does not show 6 GHz enabled" in html
        assert "Worst symptom is non-Wi-Fi interference, not AP-to-AP overlap" in html
        assert "Do not remove or replace APs solely because this band is saturated by non-Wi-Fi energy" in html
        assert "Current RF profile: Auditorium (exact AP assignment); 8 dBm min; 14 dBm max; low power ceiling" in html

    def test_dated_complete_report_filename(self):
        from reporting.app import _dated_report_name
        filename = _dated_report_name(
            "William Penn Charter School",
            "Complete",
            datetime(2026, 5, 2, 21, 30),
            "pdf",
        )
        assert filename == "William_Penn_Charter_School_Complete_Report_2026-05-02.pdf"

    def test_fixed_run_timestamp_makes_report_html_repeatable(self, monkeypatch):
        from reporting.app import build_org_report
        monkeypatch.setenv("MERAKI_REPORT_FIXED_NOW", "2026-05-02T21:30:00")
        first = build_org_report(FIXTURES, "Test Org")
        second = build_org_report(FIXTURES, "Test Org")
        assert first == second
        assert "Generated May 2, 2026 at 9:30 PM" in first

    def test_disconnected_ports_are_not_reported_as_issues(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "switch_port_statuses.json").write_text(
            json.dumps(
                {
                    "Q2SW-TEST-0001": [
                        {
                            "portId": "1",
                            "status": "disconnected",
                            "errors": ["Port disconnected"],
                            "warnings": [],
                            "speed": "",
                            "duplex": "",
                            "poeMode": "auto",
                            "isUplink": False,
                        },
                        {
                            "portId": "2",
                            "status": "connected",
                            "errors": ["CRC errors detected"],
                            "warnings": [],
                            "speed": "1 Gbps",
                            "duplex": "full",
                            "poeMode": "auto",
                            "isUplink": False,
                        },
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Port Test")
        assert "CRC errors detected" in html
        assert "Core-SW-1" in html
        assert "Port disconnected" not in html

    def test_disconnected_ports_do_not_get_deep_dive_error_badges(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "switch_port_statuses.json").write_text(
            json.dumps(
                {
                    "Q2SW-TEST-0001": [
                        {
                            "portId": "1",
                            "status": "disconnected",
                            "errors": ["Port disconnected"],
                            "warnings": [],
                            "speed": "",
                            "duplex": "",
                            "poeMode": "auto",
                            "isUplink": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Disconnected Deep Dive Test")
        assert "Port disconnected" not in html
        assert ">1 error(s)<" not in html

    def test_100_gbps_ports_are_not_low_speed_warnings(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "switch_port_statuses.json").write_text(
            json.dumps(
                {
                    "Q2SW-TEST-0001": [
                        {
                            "portId": "49",
                            "status": "connected",
                            "errors": [],
                            "warnings": [],
                            "speed": "100 Gbps",
                            "duplex": "full",
                            "isUplink": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Speed Test")
        assert "100G" in html
        assert 'badge-warn">100 Gbps' not in html

    def test_mesh_no_repeater_404_is_suppressed(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        (tmp_path / "wireless_mesh_statuses.json").write_text(
            json.dumps(
                {
                    "N_test_001": {
                        "error": 'HTTP 404 for https://api.meraki.com/api/v1/networks/N_test/wireless/meshStatuses: {"errors":["No MR repeaters found on this network"]}'
                    }
                }
            ),
            encoding="utf-8",
        )

        html = build_org_report(str(tmp_path), "Mesh Test")
        assert "No MR repeaters found" not in html
        assert "Mesh Status Notes" not in html

    def test_ap_rows_do_not_render_empty_model_code_tags(self, tmp_path):
        from reporting.app import build_org_report

        for fn in os.listdir(FIXTURES):
            src = os.path.join(FIXTURES, fn)
            dst = tmp_path / fn
            if os.path.isfile(src):
                shutil.copy(src, dst)

        html = build_org_report(str(tmp_path), "AP Model Test")
        assert "<code></code>" not in html


class TestHealthCardRatings:
    """Unit test the health domain scoring logic independently."""

    def _run_health(self, **overrides):
        """Build a minimal args set and return the health card variables."""
        from reporting.app import build_org_report
        # Just build the full report and check the rendered HTML for badges
        html = build_org_report(FIXTURES, "Test Org")
        return html

    def test_availability_card_present(self):
        html = self._run_health()
        assert "Availability" in html

    def test_licensing_card_shows_crit_when_expired(self):
        html = self._run_health()
        # Fixture has 1 expired license key — card should show crit
        assert "hcard-crit" in html or "Expired" in html


class TestLicensingCounts:
    """Test that licensing expired/active counts use the bool expired field."""

    def test_expired_bool_counted(self):
        import json, tempfile, os
        from reporting.app import build_org_report
        # Create a temp dir with just enough to not crash
        with tempfile.TemporaryDirectory() as tmp:
            # Copy all fixtures except licensing.json
            for fn in os.listdir(FIXTURES):
                src = os.path.join(FIXTURES, fn)
                dst = os.path.join(tmp, fn)
                if fn != "licensing.json":
                    import shutil
                    shutil.copy(src, dst)
            # Write a licensing.json with only bool-expired licenses
            lic = {"licenseMode": "co-term", "licenses": [
                {"key": "K1", "expired": True, "invalidated": False,
                 "startedAt": "2020-01-01T00:00:00Z", "duration": 365,
                 "counts": [{"count": 5, "model": "MR Enterprise"}],
                 "editions": [{"edition": "Enterprise", "productType": "wireless"}]},
                {"key": "K2", "expired": False, "invalidated": False,
                 "startedAt": "2025-01-01T00:00:00Z", "duration": 1095,
                 "counts": [{"count": 10, "model": "MR Enterprise"}],
                 "editions": [{"edition": "Enterprise", "productType": "wireless"}]},
            ]}
            with open(os.path.join(tmp, "licensing.json"), "w") as f:
                json.dump(lic, f)
            html = build_org_report(tmp, "Lic Test")
        # 1 expired key should trigger crit rating
        assert "hcard-crit" in html or "Expired" in html
        # K1 expired, K2 active — both should appear in the licensing table
        assert "K1" in html
        assert "K2" in html
