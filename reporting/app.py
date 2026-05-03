#!/usr/bin/env python3
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .common import (
    BACKUPS_DIR,
    REPORT_VERSION,
    _he,
    _hardware_consistency_note,
    _model_capability_summary,
    build_fallback_security_checks,
    check_backup_schema,
    find_org_dirs,
    load_json,
    md_to_html,
    render_kpi_row,
    render_section,
    render_security_baseline,
)
from .topology import _topo_pages, _topo_summary_rows, _topo_svg
from .sections import (
    _build_ap_interference_section,
    _build_budget_forecast_section,
    _build_config_coverage_section,
    _build_switch_detail_section,
    _build_wan_capacity_section,
)
from .html_shell import build_html, write_pdf

log = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _report_slug(name: str) -> str:
    slug = re.sub(r"[^\w]+", "_", name or "Site").strip("_")
    return slug or "Site"


def _dated_report_name(org_name: str, label: str, run_ts: datetime, ext: str) -> str:
    date_stamp = run_ts.strftime("%Y-%m-%d")
    return f"{_report_slug(org_name)}_{label}_Report_{date_stamp}.{ext}"

def build_org_report(
    org_dir: str,
    org_name: str,
    exec_purpose: str = "",
    report_kind: str = "full",
) -> str:
    # ── Schema compatibility check ────────────────────────────────────────────
    _schema_warnings = check_backup_schema(org_dir)
    _schema_banner = ""
    if _schema_warnings:
        for _w in _schema_warnings:
            log.warning("Schema: %s", _w)
        _schema_banner = (
            '<div class="schema-warning-banner">'
            "<strong>⚠ Backup compatibility notice:</strong> "
            + " ".join(_he(w) for w in _schema_warnings)
            + "</div>"
        )

    rec_path = os.path.join(org_dir, "recommendations.md")
    rec_md = ""
    if os.path.exists(rec_path):
        with open(rec_path, "r", encoding="utf-8") as f:
            rec_md = f.read()

    # Load all relevant data files
    inventory_summary = load_json(os.path.join(org_dir, "inventory_summary.json")) or {}
    poe_summary = load_json(os.path.join(org_dir, "poe_power_summary.json")) or {}
    channel_util = (
        load_json(os.path.join(org_dir, "channel_utilization_by_device.json")) or {}
    )
    devices_avail = (
        load_json(os.path.join(org_dir, "devices_availabilities.json")) or []
    )
    lldp_cdp = load_json(os.path.join(org_dir, "lldp_cdp.json")) or {}
    wireless_stats = (
        load_json(os.path.join(org_dir, "wireless_connection_stats.json")) or {}
    )
    # wireless_clients.json is {net_id: [client, …]} — flatten to a single list
    _wc_raw = load_json(os.path.join(org_dir, "wireless_clients.json")) or {}
    if isinstance(_wc_raw, dict):
        wireless_clients = [
            cl for clients in _wc_raw.values()
            if isinstance(clients, list)
            for cl in clients
            if isinstance(cl, dict)
        ]
    elif isinstance(_wc_raw, list):
        wireless_clients = [cl for cl in _wc_raw if isinstance(cl, dict)]
    else:
        wireless_clients = []
    switch_port_statuses_by_switch = (
        load_json(os.path.join(org_dir, "switch_port_statuses.json")) or {}
    )
    switch_port_configs_by_switch = (
        load_json(os.path.join(org_dir, "switch_port_configs.json")) or {}
    )
    uplink_statuses = load_json(os.path.join(org_dir, "uplink_statuses.json")) or []
    appliance_uplinks_usage = load_json(os.path.join(org_dir, "appliance_uplinks_usage.json")) or {}
    devices_statuses_raw = load_json(os.path.join(org_dir, "devices_statuses.json")) or []
    clients_overview_raw = load_json(os.path.join(org_dir, "clients_overview.json")) or {}
    licensing_data = load_json(os.path.join(org_dir, "licensing.json")) or {}
    rf_profiles = load_json(os.path.join(org_dir, "wireless_rf_profiles.json")) or {}
    inventory_devices = load_json(os.path.join(org_dir, "inventory_devices.json")) or []
    firmware_upgrades = load_json(os.path.join(org_dir, "firmware_upgrades.json")) or []
    wireless_settings = load_json(os.path.join(org_dir, "wireless_settings.json")) or {}
    wireless_ssids = load_json(os.path.join(org_dir, "wireless_ssids.json")) or {}
    alerts_history = load_json(os.path.join(org_dir, "alerts_history.json")) or {}
    wireless_mesh_statuses = load_json(os.path.join(org_dir, "wireless_mesh_statuses.json")) or {}
    pricing_payload = (
        load_json(os.path.join(org_dir, "pricing.json"))
        or load_json(os.path.join(BASE_DIR, "pricing.json"))
        or {}
    )

    # switch_port_configs / statuses are {serial: [port, …]} dicts — flatten,
    # injecting switchSerial so downstream code can reference the parent switch.
    def _flatten_ports(path: str) -> List[Dict]:
        raw = load_json(path) or {}
        if isinstance(raw, list):
            return raw
        result = []
        for serial, ports in raw.items():
            if isinstance(ports, list):
                for p in ports:
                    if isinstance(p, dict):
                        p.setdefault("switchSerial", serial)
                        result.append(p)
        return result

    switch_port_configs = _flatten_ports(
        os.path.join(org_dir, "switch_port_configs.json")
    )
    switch_port_statuses = _flatten_ports(
        os.path.join(org_dir, "switch_port_statuses.json")
    )
    networks = load_json(os.path.join(org_dir, "networks.json")) or []
    security_baseline = load_json(os.path.join(org_dir, "security_baseline.json")) or {}
    org_dirs = find_org_dirs(BACKUPS_DIR)
    network_names = {
        n.get("id"): n.get("name", n.get("id", ""))
        for n in networks
        if isinstance(n, dict) and n.get("id")
    }

    def _parse_dt(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    eox_devices = []
    lifecycle_by_network: Dict[str, Dict[str, int]] = {}
    if isinstance(inventory_devices, list):
        for dev in inventory_devices:
            if not isinstance(dev, dict):
                continue
            eox = dev.get("eox") or {}
            status = (eox.get("status") or "").lower()
            net_name = network_names.get(dev.get("networkId"), "Unassigned")
            if status:
                lifecycle_by_network.setdefault(net_name, {})
                lifecycle_by_network[net_name][status] = lifecycle_by_network[net_name].get(status, 0) + 1
            if status and status != "active":
                eox_devices.append({
                    "name": dev.get("name") or dev.get("model") or dev.get("serial", ""),
                    "model": dev.get("model", ""),
                    "serial": dev.get("serial", ""),
                    "network": network_names.get(dev.get("networkId"), "Unassigned"),
                    "status": eox.get("status"),
                    "endOfSale": eox.get("endOfSaleAt"),
                    "endOfSupport": eox.get("endOfSupportAt"),
                })

    # Device availability analysis
    device_status_counts: Dict[str, int] = {}
    device_type_counts: Dict[str, int] = {}
    for device in devices_avail:
        status = device.get("status", "unknown")
        product_type = device.get("productType", "unknown")
        device_status_counts[status] = device_status_counts.get(status, 0) + 1
        device_type_counts[product_type] = device_type_counts.get(product_type, 0) + 1

    # PoE analysis
    poe_ports = (
        poe_summary.get("port_poe_totals", []) if isinstance(poe_summary, dict) else []
    )
    poe_switches = (
        poe_summary.get("switch_poe_totals", [])
        if isinstance(poe_summary, dict)
        else []
    )
    poe_by_serial = {s.get("serial", ""): s for s in poe_switches}

    # Channel utilization analysis
    if isinstance(channel_util, list):
        high_util_devices = [
            d for d in channel_util if float(d.get("utilizationTotal", 0)) > 70
        ]
        moderate_util_devices = [
            d for d in channel_util if 30 <= float(d.get("utilizationTotal", 0)) <= 70
        ]
        ap_util_by_serial = {d.get("serial", ""): d for d in channel_util}
    else:
        high_util_devices = []
        moderate_util_devices = []
        ap_util_by_serial = {}

    # Build AP-to-switch mapping from LLDP/CDP data
    # lldp_cdp: {switch_serial: {ports: {port_id: {lldp/cdp: {...}}}}}
    _mac_to_serial: Dict[str, str] = {
        d.get("mac", "").lower().replace(":", ""): d.get("serial", "")
        for d in devices_avail
        if d.get("mac") and d.get("serial")
    }
    ap_to_switch: Dict[str, Dict] = {}   # ap_serial -> {switch, port}
    switch_to_aps: Dict[str, list] = {}  # switch_serial -> [ap_serial, ...]
    if isinstance(lldp_cdp, dict):
        for sw_serial, sw_lldp in lldp_cdp.items():
            if not isinstance(sw_lldp, dict):
                continue
            for port_id, port_data in sw_lldp.get("ports", {}).items():
                if not isinstance(port_data, dict):
                    continue
                _lldp = port_data.get("lldp") or {}
                _cdp  = port_data.get("cdp")  or {}
                _desc = (
                    _lldp.get("systemDescription")
                    or _cdp.get("platform")
                    or ""
                ).lower()
                if "mr" not in _desc and "access point" not in _desc:
                    continue
                _mac = (
                    _lldp.get("chassisId")
                    or port_data.get("deviceMac", "")
                ).lower().replace(":", "").replace("-", "")
                _ap_serial = _mac_to_serial.get(_mac)
                if _ap_serial:
                    ap_to_switch[_ap_serial] = {
                        "switch": sw_serial,
                        "port": port_id,
                        "name": _lldp.get("systemName") or _cdp.get("deviceId") or "",
                    }
                    switch_to_aps.setdefault(sw_serial, []).append(_ap_serial)

    # Flatten wireless_stats to ap_serial -> connectionStats
    # wireless_stats: {net_id: [{serial, connectionStats}]}
    ap_conn_stats: Dict[str, Dict] = {}
    if isinstance(wireless_stats, dict):
        for _net_id, _ap_list in wireless_stats.items():
            if isinstance(_ap_list, list):
                for _entry in _ap_list:
                    if isinstance(_entry, dict) and _entry.get("serial"):
                        ap_conn_stats[_entry["serial"]] = _entry.get("connectionStats", {})

    # Switch port issue analysis
    # Note: the Meraki API returns "errors" and "warnings" as lists of strings, not integers.
    switch_port_issues = []
    if isinstance(switch_port_statuses, list):
        for port in switch_port_statuses[:100]:
            port_errors = port.get("errors") or []  # always a list
            if isinstance(port_errors, str):
                port_errors = [port_errors]
            port_warnings = port.get("warnings") or []
            if isinstance(port_warnings, str):
                port_warnings = [port_warnings]
            speed_raw = port.get("speed") or ""
            # speed may be "10 Mbps", "100 Mbps", 10, 100, etc.
            speed_num = None
            try:
                speed_num = int(str(speed_raw).split()[0])
            except (ValueError, IndexError):
                pass
            is_uplink = bool(port.get("isUplink"))
            if any(
                [
                    bool(port_errors),
                    is_uplink and speed_num in [10, 100],
                ]
            ):
                switch_port_issues.append(
                    {
                        "switch": port.get("switchSerial", "Unknown"),
                        "port": port.get("portId", "Unknown"),
                        "errors": port_errors,          # list of strings
                        "error_count": len(port_errors),
                        "warning_count": len(port_warnings),
                        "speed": speed_raw,
                        "duplex": port.get("duplex", "Unknown"),
                        "poeMode": port.get("poeMode", "Unknown"),
                        "status": port.get("status", "Unknown"),
                        "isUplink": is_uplink,
                    }
                )

    # Configuration issues
    config_issues = []
    if isinstance(switch_port_configs, list):
        for port in switch_port_configs[:100]:
            if port.get("enabled") == False and port.get("poeEnabled") == True:
                config_issues.append(
                    {
                        "switch": port.get("switchSerial", "Unknown"),
                        "port": port.get("portId", "Unknown"),
                        "issue": "PoE enabled but port disabled",
                        "type": "Configuration",
                    }
                )

    # Port issues indexed by switch serial
    port_issues_by_switch: Dict[str, list] = {}
    for issue in switch_port_issues:
        port_issues_by_switch.setdefault(issue["switch"], []).append(issue)

    networks_by_id = {
        n.get("id"): n for n in networks if isinstance(n, dict) and n.get("id")
    }

    # Group devices by network (building / site)
    devices_by_network: Dict[str, dict] = {}
    serial_to_network: Dict[str, dict] = {}
    for device in devices_avail:
        net = device.get("network") or {}
        net_id = net.get("id", "unassigned")
        net_name = (
            net.get("name")
            or (networks_by_id.get(net_id) or {}).get("name")
            or "Unassigned"
        )
        serial = device.get("serial", "")
        if net_id not in devices_by_network:
            devices_by_network[net_id] = {"name": net_name, "id": net_id, "devices": []}
        devices_by_network[net_id]["devices"].append(device)
        if serial:
            serial_to_network[serial] = {"id": net_id, "name": net_name}

    # Inventory summary
    inv_by_type = inventory_summary.get("by_type") or {}
    top_models = inventory_summary.get("top_models") or []
    total_devices = sum(inv_by_type.values()) if inv_by_type else len(devices_avail)

    # KPI items (compact row — kept for TOC/overview tables)
    online_count_v = device_status_counts.get("online", 0)
    offline_count_v = sum(v for k, v in device_status_counts.items() if k != "online")
    kpi_items = [
        ("Total Sites", str(len(networks) or len(devices_by_network))),
        ("Total Devices", str(total_devices)),
        ("Online", str(online_count_v)),
        ("Offline / Alert", str(offline_count_v)),
        ("MX Appliances", str(inv_by_type.get("appliance", 0))),
        ("MS Switches", str(inv_by_type.get("switch", 0))),
        ("MR Access Points", str(inv_by_type.get("wireless", 0))),
        ("High Util APs", str(len(high_util_devices))),
        ("Port Issues", str(len(switch_port_issues))),
        ("Config Issues", str(len(config_issues))),
    ]

    security_checks = (
        security_baseline.get("checks")
        if isinstance(security_baseline, dict) and security_baseline.get("checks")
        else build_fallback_security_checks(devices_avail, inv_by_type, switch_port_issues)
    )

    # ── Health at a Glance domain scoring ──────────────────────────────────
    def _hcard(domain: str, rating: str, stat: str, detail: str) -> str:
        """rating: 'good' | 'warn' | 'crit' | 'info'"""
        icons = {"good": "✓", "warn": "⚠", "crit": "✕", "info": "–"}
        return (
            f'<div class="health-card health-card--{rating}">'
            f'<div class="health-card-header">'
            f'<span class="health-card-icon">{icons.get(rating, "–")}</span>'
            f'<span class="health-card-domain">{_he(domain)}</span>'
            f'</div>'
            f'<div class="health-card-stat">{stat}</div>'
            f'<div class="health-card-detail">{detail}</div>'
            f'</div>'
        )

    # Availability
    avail_pct = round(100 * online_count_v / max(total_devices, 1))
    if avail_pct >= 98:
        _avail_rating = "good"
    elif avail_pct >= 90:
        _avail_rating = "warn"
    else:
        _avail_rating = "crit"
    _avail_card = _hcard(
        "Availability", _avail_rating,
        f"{avail_pct}% online",
        f"{online_count_v} of {total_devices} devices",
    )

    # Wireless / RF
    _ap_total = inv_by_type.get("wireless", 0)
    _high_ap = len(high_util_devices)
    _mod_ap  = len(moderate_util_devices)
    if _high_ap > max(1, _ap_total * 0.15):
        _rf_rating = "crit"
    elif _high_ap > 0:
        _rf_rating = "warn"
    else:
        _rf_rating = "good" if _ap_total > 0 else "info"
    _rf_card = _hcard(
        "Wireless / RF", _rf_rating,
        f"{_high_ap} high-util AP{'s' if _high_ap != 1 else ''}",
        f"{_mod_ap} moderate · {_ap_total} total APs",
    )

    # Switching
    _sw_issues = len(switch_port_issues)
    _cfg_issues = len(config_issues)
    if _sw_issues > 5 or _cfg_issues > 5:
        _sw_rating = "crit"
    elif _sw_issues > 0 or _cfg_issues > 0:
        _sw_rating = "warn"
    else:
        _sw_rating = "good" if inv_by_type.get("switch", 0) > 0 else "info"
    _sw_card = _hcard(
        "Switching", _sw_rating,
        f"{_sw_issues} port issue{'s' if _sw_issues != 1 else ''}",
        f"{_cfg_issues} config anomal{'ies' if _cfg_issues != 1 else 'y'} · {inv_by_type.get('switch', 0)} switches",
    )

    # WAN
    _wan_active = sum(
        1 for u in (uplink_statuses if isinstance(uplink_statuses, list) else [])
        if isinstance(u, dict) and str(u.get("status", "")).lower() == "active"
    )
    _wan_total = sum(
        1 for u in (uplink_statuses if isinstance(uplink_statuses, list) else [])
        if isinstance(u, dict) and u.get("interface")
    )
    _wan_down = _wan_total - _wan_active
    if _wan_total == 0:
        _wan_rating, _wan_stat, _wan_detail = "info", "No WAN data", "uplink status unavailable"
    elif _wan_down > 0:
        _wan_rating = "crit" if _wan_active == 0 else "warn"
        _wan_stat = f"{_wan_down} link{'s' if _wan_down != 1 else ''} down"
        _wan_detail = f"{_wan_active} active of {_wan_total} uplinks"
    else:
        _wan_rating = "good"
        _wan_stat = f"{_wan_active} active"
        _wan_detail = f"{_wan_total} uplink{'s' if _wan_total != 1 else ''} healthy"
    _wan_card = _hcard("WAN / Internet", _wan_rating, _wan_stat, _wan_detail)

    # Security
    _sec_fail  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and c.get("status") == "fail")
    _sec_warn  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and c.get("status") == "warning")
    _sec_pass  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and c.get("status") == "pass")
    if _sec_fail > 0:
        _sec_rating = "crit"
    elif _sec_warn > 0:
        _sec_rating = "warn"
    else:
        _sec_rating = "good" if _sec_pass > 0 else "info"
    _sec_card = _hcard(
        "Security Baseline", _sec_rating,
        f"{_sec_fail} fail{'s' if _sec_fail != 1 else ''} · {_sec_warn} warn{'s' if _sec_warn != 1 else ''}",
        f"{_sec_pass} checks passed",
    )

    # Lifecycle (EOL heuristic — flag known legacy model prefixes)
    _EOL_PREFIXES = (
        "MR18", "MR24", "MR26", "MR32", "MR34",
        "MS220", "MS320", "MS420",
        "MX64", "MX65", "MX80", "MX84", "MX90", "MX400", "MX600",
    )
    _eol_models = [
        m for m, _ in top_models
        if any(str(m).upper().startswith(p) for p in _EOL_PREFIXES)
    ]
    _model_count = len(top_models)
    if _eol_models:
        _lc_rating = "crit"
        _lc_stat   = f"{len(_eol_models)} EOL model{'s' if len(_eol_models) != 1 else ''}"
        _lc_detail = ", ".join(_eol_models[:4]) + (" …" if len(_eol_models) > 4 else "")
    elif _model_count > 8:
        _lc_rating = "warn"
        _lc_stat   = f"{_model_count} distinct models"
        _lc_detail = "high hardware fragmentation"
    else:
        _lc_rating = "good" if _model_count > 0 else "info"
        _lc_stat   = f"{_model_count} model{'s' if _model_count != 1 else ''}"
        _lc_detail = "no known EOL hardware flagged"
    _lc_card = _hcard("Lifecycle / Hardware", _lc_rating, _lc_stat, _lc_detail)

    # Licensing
    _lic_mode = licensing_data.get("licenseMode") if isinstance(licensing_data, dict) else None
    _lic_list = licensing_data.get("licenses", []) if isinstance(licensing_data, dict) else []
    # co-term licenses use an `expired` bool; per-device licenses use a `status` string
    _lic_expired = sum(
        1 for lic in _lic_list
        if isinstance(lic, dict) and (
            lic.get("expired") is True
            or str(lic.get("status", "")).lower() in ("expired", "inactive")
        )
    )
    _lic_active = sum(
        1 for lic in _lic_list
        if isinstance(lic, dict) and not lic.get("invalidated") and (
            lic.get("expired") is False
            or str(lic.get("status", "")).lower() in ("ok", "active", "in compliance")
        )
    )
    if isinstance(licensing_data, dict) and licensing_data.get("error"):
        _lic_rating, _lic_stat, _lic_detail = "info", "Data unavailable", "license API not accessible"
    elif _lic_expired > 0:
        _lic_rating = "crit"
        _lic_stat   = f"{_lic_expired} expired"
        _lic_detail = f"{_lic_active} active · {_lic_mode or 'unknown'} model"
    elif _lic_active > 0:
        _lic_rating = "good"
        _lic_stat   = f"{_lic_active} active"
        _lic_detail = f"{_lic_mode or 'co-term'} licensing"
    else:
        _lic_rating, _lic_stat, _lic_detail = "info", "No detail", f"{_lic_mode or 'unknown'} model"
    _lic_card = _hcard("Licensing", _lic_rating, _lic_stat, _lic_detail)

    health_grid_html = (
        '<div class="health-grid">'
        + _avail_card + _rf_card + _sw_card + _wan_card
        + _sec_card + _lc_card + _lic_card
        + '</div>'
    )

    # =========================================================
    # COVER PAGE
    # =========================================================
    _now = datetime.now()
    _report_date = _now.strftime("%B %d, %Y")
    _report_ts = _now.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    cover_html = f"""
    <section class="cover">
      <div class="cover-inner">
        <div class="cover-top">
          <div class="cover-brand">Techmore</div>
          <div class="cover-rule"></div>
          <div class="cover-title">Network Health &amp;<br>Optimization Report</div>
          <div class="cover-subtitle">{_he(org_name)}</div>
          <div class="cover-run-ts">Generated {_report_ts}</div>
        </div>
        <div class="cover-bottom">
          <div class="cover-bottom-rule"></div>
          <div class="cover-bottom-info">
            <span class="cover-conf">Confidential &mdash; Prepared by Techmore</span>
            <span class="cover-ver-date">v{REPORT_VERSION} &nbsp;&bull;&nbsp; {_report_date}</span>
          </div>
        </div>
      </div>
    </section>
    """

    # =========================================================
    # TABLE OF CONTENTS PAGE
    # =========================================================
    toc_site_items = "".join(
        f'<li class="toc-sub-item">{net_data["name"]}</li>'
        for net_data in sorted(devices_by_network.values(), key=lambda x: x["name"])
    )
    switch_deep_dive_html, toc_switch_items = _build_switch_detail_section(
        devices_by_network,
        lldp_cdp,
        switch_port_statuses_by_switch,
        switch_port_configs_by_switch,
        poe_by_serial,
        port_issues_by_switch,
    )
    ap_interference_html = _build_ap_interference_section(
        devices_by_network,
        channel_util,
        wireless_stats,
        switch_port_statuses_by_switch,
    )
    config_coverage_html = _build_config_coverage_section(org_dir, networks)
    budget_forecast_html = _build_budget_forecast_section(inventory_summary, pricing_payload)
    wan_capacity_html = _build_wan_capacity_section(
        uplink_statuses,
        appliance_uplinks_usage,
        devices_avail,
        networks_by_id,
    )
    toc_switch_subitems = "".join(
        f'<li class="toc-sub-item"><a href="#{_he(anchor)}">{_he(label)}</a></li>'
        for anchor, label in toc_switch_items
    )
    toc_html = f"""
    <section class="toc-page">
      <div class="toc-header">Table of Contents</div>
      <ol class="toc-list">
        <li>
          <span class="toc-num">1</span>
          <span class="toc-entry">Executive Summary</span>
        </li>
        <li>
          <span class="toc-num">2</span>
          <span class="toc-entry">Network Overview</span>
        </li>
        <li>
          <span class="toc-num">3</span>
          <span class="toc-entry">Network Topology</span>
          <ol class="toc-sub">
            {toc_site_items}
          </ol>
        </li>
        <li>
          <span class="toc-num">4</span>
          <span class="toc-entry">Traffic Flows &amp; Bottleneck Analysis</span>
        </li>
        <li>
          <span class="toc-num">5</span>
          <span class="toc-entry">Device Health &amp; Issues</span>
        </li>
        <li>
          <span class="toc-num">6</span>
          <span class="toc-entry">PoE Power Analysis</span>
        </li>
        <li>
          <span class="toc-num">7</span>
          <span class="toc-entry">Security Baseline</span>
        </li>
        <li>
          <span class="toc-num">8</span>
          <span class="toc-entry">Recommendations &amp; Implementation Plan</span>
        </li>
        <li>
          <span class="toc-num">9</span>
          <span class="toc-entry">CIS 8 Controls Assessment</span>
        </li>
        <li>
          <span class="toc-num">10</span>
          <span class="toc-entry">Licensing Summary</span>
        </li>
        <li>
          <span class="toc-num">11</span>
          <span class="toc-entry">Configuration Backup Coverage</span>
        </li>
        <li>
          <span class="toc-num">12</span>
          <span class="toc-entry">Hardware Cost &amp; Refresh Plan</span>
        </li>
        <li>
          <span class="toc-num">13</span>
          <span class="toc-entry">Internet Capacity &amp; Utilization</span>
        </li>
        <li>
          <span class="toc-num">14</span>
          <span class="toc-entry">AP Interference Audit</span>
        </li>
        <li>
          <span class="toc-num">15</span>
          <span class="toc-entry">Client Analysis</span>
        </li>
        <li>
          <span class="toc-num">16</span>
          <span class="toc-entry">Switch Deep Dive</span>
          <ol class="toc-sub">
            {toc_switch_subitems}
          </ol>
        </li>
      </ol>
    </section>
    """

    toc_backup_html = f"""
    <section class="toc-page">
      <div class="toc-header">Table of Contents</div>
      <ol class="toc-list">
        <li>
          <span class="toc-num">1</span>
          <span class="toc-entry">Network Overview</span>
        </li>
        <li>
          <span class="toc-num">2</span>
          <span class="toc-entry">Network Topology</span>
          <ol class="toc-sub">
            {toc_site_items}
          </ol>
        </li>
        <li>
          <span class="toc-num">3</span>
          <span class="toc-entry">Traffic Flows &amp; Bottleneck Analysis</span>
        </li>
        <li>
          <span class="toc-num">4</span>
          <span class="toc-entry">Device Health &amp; Issues</span>
        </li>
        <li>
          <span class="toc-num">5</span>
          <span class="toc-entry">PoE Power Analysis</span>
        </li>
        <li>
          <span class="toc-num">6</span>
          <span class="toc-entry">Security Baseline</span>
        </li>
        <li>
          <span class="toc-num">7</span>
          <span class="toc-entry">Recommendations &amp; Implementation Plan</span>
        </li>
        <li>
          <span class="toc-num">8</span>
          <span class="toc-entry">CIS 8 Controls Assessment</span>
        </li>
        <li>
          <span class="toc-num">9</span>
          <span class="toc-entry">Licensing Summary</span>
        </li>
        <li>
          <span class="toc-num">10</span>
          <span class="toc-entry">Configuration Backup Coverage</span>
        </li>
        <li>
          <span class="toc-num">11</span>
          <span class="toc-entry">Hardware Cost &amp; Refresh Plan</span>
        </li>
        <li>
          <span class="toc-num">12</span>
          <span class="toc-entry">Internet Capacity &amp; Utilization</span>
        </li>
        <li>
          <span class="toc-num">13</span>
          <span class="toc-entry">AP Interference Audit</span>
        </li>
        <li>
          <span class="toc-num">14</span>
          <span class="toc-entry">Client Analysis</span>
        </li>
        <li>
          <span class="toc-num">15</span>
          <span class="toc-entry">Switch Deep Dive</span>
          <ol class="toc-sub">
            {toc_switch_subitems}
          </ol>
        </li>
      </ol>
    </section>
    """

    # =========================================================
    # SECTION 1: EXECUTIVE SUMMARY  (fills its own page)
    # =========================================================
    online_count = device_status_counts.get("online", 0)
    availability_pct = round(100 * online_count / total_devices) if total_devices else 0
    _offline_count = total_devices - online_count

    # Build a prioritized risk list from health card ratings
    _risk_bullets: list[str] = []
    _prio_bullets: list[str] = []

    if _avail_rating == "crit":
        _risk_bullets.append(
            f"<strong>Device availability is critical</strong> — {_offline_count} of "
            f"{total_devices} devices offline or alerting ({availability_pct}% online). "
            "Investigate offline units immediately; cloud management and SD-WAN path "
            "selection depend on appliance reachability."
        )
        _prio_bullets.append(
            "<strong>Immediate (0–2 weeks):</strong> Triage offline devices, confirm "
            "connectivity to Meraki Dashboard, and restore any degraded links."
        )
    elif _avail_rating == "warn":
        _risk_bullets.append(
            f"<strong>Availability is below target</strong> — {_offline_count} device(s) "
            f"offline, bringing availability to {availability_pct}%. Monitor closely and "
            "escalate if the count increases."
        )

    if _rf_rating == "crit":
        _risk_bullets.append(
            f"<strong>Wireless RF congestion detected</strong> — {len(high_util_devices)} "
            "access point(s) exceeding 70% channel utilization. Dense AP deployments or "
            "insufficient 5 GHz client steering are the most common causes. Congestion at "
            "this level degrades throughput and roaming quality for all associated clients."
        )
        _prio_bullets.append(
            "<strong>Short-term (2–6 weeks):</strong> Audit high-utilization APs — reduce "
            "SSID count, enable band steering, rebalance channel plan, or add APs to relieve "
            "congested cells."
        )
    elif _rf_rating == "warn":
        _risk_bullets.append(
            f"<strong>Wireless RF utilization is elevated</strong> — {len(high_util_devices)} "
            "AP(s) above 70% utilization. Proactive channel and SSID tuning is advised "
            "before utilization climbs further."
        )

    if _sw_rating in ("crit", "warn"):
        _risk_bullets.append(
            f"<strong>Switch port issues require attention</strong> — {len(switch_port_issues)} "
            "port(s) with errors or sub-gigabit uplinks detected. Frame errors and duplex "
            "mismatches can introduce latency and packet loss that affects every device "
            "downstream of the affected port."
        )
        _prio_bullets.append(
            "<strong>Short-term (2–6 weeks):</strong> Resolve switch port errors and duplex "
            "mismatches; replace cabling or SFPs where hardware faults are confirmed."
        )

    if _lc_rating in ("crit", "warn") and _eol_models:
        _eol_str = ", ".join(_eol_models[:4]) + (" …" if len(_eol_models) > 4 else "")
        _risk_bullets.append(
            f"<strong>End-of-life hardware in production</strong> — model(s) {_eol_str} are "
            "past or approaching Cisco Meraki end-of-support. EOL devices no longer receive "
            "firmware security patches and may lose Dashboard management access when licenses "
            "lapse. Continued operation increases security exposure and reduces operational "
            "predictability."
        )
        _prio_bullets.append(
            "<strong>Medium-term (6–12 weeks):</strong> Initiate hardware refresh planning "
            f"for EOL device(s) — {_eol_str}. Prioritize units in critical path roles "
            "(core switching, edge appliances)."
        )
    elif _lc_rating == "warn" and _model_count > 8:
        _risk_bullets.append(
            f"<strong>High hardware fragmentation</strong> — {_model_count} distinct device "
            "models detected. Fragmented hardware inventories complicate firmware management, "
            "spare-parts stocking, and consistent feature availability across the environment."
        )

    if _lic_rating == "crit" and _lic_expired > 0:
        _risk_bullets.append(
            f"<strong>Expired license keys present</strong> — {_lic_expired} license key(s) "
            "have lapsed. Expired co-term licenses can cause devices to enter limited mode, "
            "losing Dashboard visibility and security feature enforcement. Renew or re-assign "
            "before the next renewal window."
        )
        _prio_bullets.append(
            "<strong>Immediate (0–2 weeks):</strong> Review expired license keys in the "
            "Meraki Dashboard and engage your Cisco account team to assess renewal impact."
        )

    if _sec_rating in ("crit", "warn"):
        _risk_bullets.append(
            f"<strong>Security baseline gaps</strong> — {_sec_fail} check(s) failing and "
            f"{_sec_warn} warning(s). Baseline failures such as disabled AMP, IDS/IPS in "
            "detection-only mode, or exposed port forwarding represent direct threat exposure "
            "for the environment."
        )
        _prio_bullets.append(
            "<strong>Short-term (2–6 weeks):</strong> Address failing security baseline "
            "checks — enable AMP and IDS/IPS in prevention mode; review internet-exposed "
            "services."
        )

    # Long-term catch-all
    _prio_bullets.append(
        "<strong>Long-term (3–6 months):</strong> Develop a hardware refresh roadmap "
        "addressing lifecycle gaps, standardize access switching tiers, and validate "
        "licensing coverage aligns with the physical device inventory."
    )

    # Overall health rating label
    _crit_domains = [r for r in [_avail_rating, _rf_rating, _sw_rating, _wan_rating,
                                  _sec_rating, _lc_rating, _lic_rating] if r == "crit"]
    _warn_domains = [r for r in [_avail_rating, _rf_rating, _sw_rating, _wan_rating,
                                  _sec_rating, _lc_rating, _lic_rating] if r == "warn"]
    if _crit_domains:
        _overall_label = (
            f'<span class="hcard-rating hcard-crit">'
            f'Needs Attention — {len(_crit_domains)} Critical Domain(s)</span>'
        )
    elif _warn_domains:
        _overall_label = (
            f'<span class="hcard-rating hcard-warn">'
            f'Monitor — {len(_warn_domains)} Warning(s)</span>'
        )
    else:
        _overall_label = '<span class="hcard-rating hcard-good">Healthy</span>'

    _risk_html = (
        "<ul>" + "".join(f"<li>{b}</li>" for b in _risk_bullets) + "</ul>"
        if _risk_bullets
        else "<p>No critical or warning-level findings were identified in this scan.</p>"
    )
    _prio_html = "<ol>" + "".join(f"<li>{b}</li>" for b in _prio_bullets) + "</ol>"

    # LLM purpose override
    _purpose_body = (
        exec_purpose
        if exec_purpose
        else (
            f"This network audit report covers the <strong>{_he(org_name)}</strong> Cisco Meraki "
            f"environment as of {_report_date}. It is prepared for IT leadership, operations "
            "teams, and decision makers who need a clear view of current network health, risk "
            "posture, lifecycle status, and near-term action priorities. Each section provides "
            "observed findings, interpreted risk, and prioritized recommendations. Where data "
            "was unavailable at collection time, findings are noted as partial or pending."
        )
    )

    exec_html = f"""
    <section id="executive-summary" class="report-section exec-full-page">
      <h2>1. Executive Summary</h2>

      <div class="summary-card exec-purpose-card">
        <div class="summary-body">{_purpose_body}</div>
      </div>

      <h3>Current State Assessment</h3>
      <div class="summary-card">
        <div class="summary-body">
          The <strong>{_he(org_name)}</strong> network spans
          <strong>{len(devices_by_network)}</strong> site(s) with a total of
          <strong>{total_devices}</strong> cloud-managed Meraki devices:
          {inv_by_type.get("appliance", 0)} MX security appliance(s),
          {inv_by_type.get("switch", 0)} MS switch(es), and
          {inv_by_type.get("wireless", 0)} MR access point(s).
          At the time of this report, <strong>{online_count}</strong> of {total_devices} devices
          ({availability_pct}%) were online and reporting to Dashboard.
          Overall environment health: {_overall_label}
          <br><br>
          {_hardware_consistency_note(top_models)}
        </div>
      </div>

      <h3>Top Operational Risks</h3>
      <div class="summary-card">
        <div class="summary-body">
          {_risk_html}
        </div>
      </div>

      <h3>Recommended Priorities</h3>
      <div class="summary-card">
        <div class="summary-body">
          {_prio_html}
        </div>
      </div>

      <h3>Infrastructure Inventory</h3>
      <table class="data">
        <thead>
          <tr>
            <th>Layer</th>
            <th>Device Type</th>
            <th>Count</th>
            <th>Role in Network</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>WAN / Edge</strong></td>
            <td>MX Security Appliance</td>
            <td>{inv_by_type.get("appliance", 0)}</td>
            <td>Internet gateway, stateful firewall, site-to-site and client VPN,
                DHCP/DNS, content filtering, and SD-WAN path selection.
                All ingress/egress traffic passes through the MX.</td>
          </tr>
          <tr>
            <td><strong>Distribution / Access</strong></td>
            <td>MS Ethernet Switch</td>
            <td>{inv_by_type.get("switch", 0)}</td>
            <td>Wired LAN switching, VLAN segmentation, 802.1Q trunking,
                PoE power delivery for APs and IP devices, and port-level
                access control via ACLs or 802.1X.</td>
          </tr>
          <tr>
            <td><strong>Wireless</strong></td>
            <td>MR Access Point</td>
            <td>{inv_by_type.get("wireless", 0)}</td>
            <td>802.11 wireless on 2.4 GHz and 5 GHz, automatic RF management,
                seamless client roaming, and SSID-to-VLAN mapping for
                traffic segmentation.</td>
          </tr>
        </tbody>
      </table>

      <h3>Health at a Glance</h3>
      {health_grid_html}
      {render_kpi_row(kpi_items)}
    </section>
    """

    # =========================================================
    # SECTION 2: NETWORK OVERVIEW (functional ordering)
    # =========================================================
    network_overview_rows = []
    network_hardware_rows = []
    for net_id, net_data in sorted(
        devices_by_network.items(), key=lambda x: x[1]["name"]
    ):
        nd = net_data["devices"]
        appliances = [d for d in nd if d.get("productType") == "appliance"]
        switches = [d for d in nd if d.get("productType") == "switch"]
        aps = [d for d in nd if d.get("productType") == "wireless"]
        online = len([d for d in nd if d.get("status") == "online"])
        total = len(nd)
        net_ap_serials = {d.get("serial") for d in aps}
        net_high_util = len(
            [d for d in high_util_devices if d.get("serial") in net_ap_serials]
        )
        offline = total - online

        badges = []
        if offline > 0:
            badges.append(f'<span class="badge badge-fail">{offline} offline</span>')
        if net_high_util > 0:
            badges.append(
                f'<span class="badge badge-warn">{net_high_util} AP high util</span>'
            )
        if not badges:
            badges.append('<span class="badge badge-ok">Healthy</span>')

        network_overview_rows.append(
            f"<tr>"
            f"<td><strong>{net_data['name']}</strong></td>"
            f"<td>{len(appliances)}</td>"
            f"<td>{len(switches)}</td>"
            f"<td>{len(aps)}</td>"
            f"<td>{online}/{total}</td>"
            f"<td>{'&nbsp;'.join(badges)}</td>"
            f"</tr>"
        )

        def _model_rollup(devs: list, max_items: int = 4) -> str:
            counts: Dict[str, int] = {}
            for dev in devs:
                model = dev.get("model") or "Unknown"
                counts[model] = counts.get(model, 0) + 1
            if not counts:
                return "—"
            items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            shown = items[:max_items]
            remainder = len(items) - len(shown)
            text = ", ".join(f"{m} ({c})" for m, c in shown)
            if remainder > 0:
                text += f" +{remainder} more"
            return text

        network_hardware_rows.append(
            f"<tr>"
            f"<td><strong>{net_data['name']}</strong></td>"
            f"<td>{_he(_model_rollup(appliances))}</td>"
            f"<td>{_he(_model_rollup(switches))}</td>"
            f"<td>{_he(_model_rollup(aps))}</td>"
            f"<td>{_he(_model_rollup([d for d in nd if d.get('productType') not in ('appliance','switch','wireless')]))}</td>"
            f"</tr>"
        )

    lifecycle_rows = []
    mixed_lifecycle_nets = []
    for net_name, counts in sorted(lifecycle_by_network.items(), key=lambda x: x[0]):
        active = counts.get("active", 0)
        near = sum(v for k, v in counts.items() if "nearendofsupport" in k)
        eos = sum(v for k, v in counts.items() if "endofsupport" in k)
        eosale = sum(v for k, v in counts.items() if "endofsale" in k)
        mixed = "Yes" if active > 0 and (near + eos + eosale) > 0 else "No"
        if mixed == "Yes":
            mixed_lifecycle_nets.append(net_name)
        lifecycle_rows.append(
            f"<tr>"
            f"<td><strong>{_he(net_name)}</strong></td>"
            f"<td>{active}</td>"
            f"<td>{near}</td>"
            f"<td>{eosale}</td>"
            f"<td>{eos}</td>"
            f"<td>{mixed}</td>"
            f"</tr>"
        )

    network_overview_html = f"""
    <section id="network-overview" class="report-section">
      <h2>2. Network Overview</h2>
      <p>Each row represents one managed network (site / building), listed in the functional
         order of the traffic path: WAN edge (MX) &rarr; switching layer (MS) &rarr;
         wireless layer (MR) &rarr; end clients.</p>
      <table class="data">
        <thead>
          <tr>
            <th>Site / Building</th>
            <th>MX Appliances</th>
            <th>MS Switches</th>
            <th>MR Access Points</th>
            <th>Devices Online</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {"".join(network_overview_rows) if network_overview_rows else '<tr><td colspan="6" class="empty-state">No devices found in this organization\'s backup. Run a full backup to populate this section.</td></tr>'}
        </tbody>
      </table>
      <h3>Hardware Inventory by Site</h3>
      <table class="data">
        <thead>
          <tr>
            <th>Site / Building</th>
            <th>MX Models</th>
            <th>MS Models</th>
            <th>MR Models</th>
            <th>Other</th>
          </tr>
        </thead>
        <tbody>
          {"".join(network_hardware_rows) if network_hardware_rows else '<tr><td colspan="5" class="empty-state">No hardware inventory data available for site-level breakdown.</td></tr>'}
        </tbody>
      </table>
      <h3>Lifecycle Mix by Site (EOX Signals)</h3>
      <table class="data">
        <thead>
          <tr>
            <th>Site / Building</th>
            <th>Active</th>
            <th>Near End of Support</th>
            <th>End of Sale</th>
            <th>End of Support</th>
            <th>Mixed Generations</th>
          </tr>
        </thead>
        <tbody>
          {"".join(lifecycle_rows) if lifecycle_rows else '<tr><td colspan="6" class="empty-state">No EOX lifecycle data available in this backup.</td></tr>'}
        </tbody>
      </table>
      <h3>Model Inventory &amp; Capabilities</h3>
      <table class="data">
        <thead>
          <tr>
            <th>Model</th><th>Count</th><th>Capability Summary</th>
          </tr>
        </thead>
        <tbody>
          {"".join(f"<tr><td>{_he(model)}</td><td>{count}</td><td>{_he(_model_capability_summary(model))}</td></tr>" for model, count in top_models[:12]) if top_models else '<tr><td colspan="3" class="empty-state">No model inventory data available.</td></tr>'}
        </tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">PoE Budget Note</div>
        <div class="summary-body">
          Current backups include measured PoE consumption and per-port allocation signals, but
          they do not yet include authoritative switch maximum PoE budget values. The report can
          therefore show actual draw and PoE-heavy switches today, but budget headroom remains an
          API collection gap that should be added to the backup pipeline before final capacity
          planning or switch replacement decisions are made.
        </div>
      </div>
    </section>
    """

    # =========================================================
    # SECTION 3: NETWORK TOPOLOGY
    # =========================================================
    topo_enrichment = {
        "device_ip": {
            d["serial"]: d.get("lanIp", "")
            for d in (devices_statuses_raw if isinstance(devices_statuses_raw, list) else [])
            if d.get("serial")
        },
        "uplink_statuses": uplink_statuses,
        "uplinks_usage": appliance_uplinks_usage,
        "clients_overview": clients_overview_raw,
        "port_configs": switch_port_configs_by_switch,
        "port_statuses": switch_port_statuses_by_switch,
    }

    topo_site_parts: List[str] = []
    for net_id, net_data in sorted(devices_by_network.items(), key=lambda x: x[1]["name"]):
        site_devs = net_data["devices"]
        if not site_devs:
            continue
        infra_devs = [
            d for d in site_devs if d.get("productType") in ("appliance", "switch")
        ]
        site_serials = {d["serial"] for d in site_devs if d.get("serial")}
        has_lldp = isinstance(lldp_cdp, dict) and any(
            s in lldp_cdp for s in site_serials
        )
        lldp_banner = (
            ""
            if has_lldp
            else (
                '<div class="topo-no-lldp">'
                "&#9432; No LLDP/CDP neighbour data found for this site. "
                "All connections are inferred from device type. "
                "Enable LLDP on switches and verify API scope to see confirmed adjacencies."
                "</div>"
            )
        )
        if not infra_devs:
            topo_body = (
                '<div class="summary-card"><div class="summary-body">'
                "No switch or MX infrastructure was present in this site slice. "
                "Wireless edge devices exist, but there is not enough switching hierarchy "
                "here to render an upstream/downstream topology tree."
                "</div></div>"
            )
        else:
            summary_rows = _topo_summary_rows(
                site_devs,
                lldp_cdp,
                switch_port_statuses_by_switch,
            )
            pages = _topo_pages(
                site_devs, lldp_cdp, ap_util_by_serial,
                port_issues_by_switch, switch_port_statuses_by_switch,
                enrichment=topo_enrichment,
            )
            topo_diagrams = ""
            for i, page in enumerate(pages):
                pb = ' style="page-break-before:always"' if i > 0 else ""
                title_html = (
                    f'<h3 class="topo-branch-title">{_he(page["title"])}</h3>'
                    if page.get("title") else ""
                )
                topo_diagrams += f'<div class="topo-diagram"{pb}>{title_html}{page["svg"]}</div>'
            topo_body = topo_diagrams + render_section(
                "Topology Summary",
                [["Device", "Model", "Upstream", "Child Switches", "Edge Devices"]]
                + summary_rows if summary_rows else [],
            )
        topo_site_parts.append(
            f'<div class="topo-site">'
            f'<h3>{_he(net_data["name"])}</h3>'
            f'{lldp_banner}'
            f'{topo_body}'
            f'</div>'
        )

    topo_legend = """
    <div class="topo-legend">
      <span class="topo-legend-item">
        <svg width="10" height="10" style="vertical-align:middle;margin-right:4px">
          <circle cx="5" cy="5" r="4" fill="#4ade80"/></svg>Online</span>
      <span class="topo-legend-item">
        <svg width="10" height="10" style="vertical-align:middle;margin-right:4px">
          <circle cx="5" cy="5" r="4" fill="#f87171"/></svg>Offline / Alert</span>
      <span class="topo-legend-item">
        <svg width="10" height="10" style="vertical-align:middle;margin-right:4px">
          <circle cx="5" cy="5" r="4" fill="#94a3b8"/></svg>Dormant</span>
      <span class="topo-legend-item">
        <svg width="22" height="10" style="vertical-align:middle;margin-right:4px">
          <line x1="0" y1="5" x2="22" y2="5" stroke="#8a9269" stroke-width="1.5"/></svg>LLDP confirmed</span>
      <span class="topo-legend-item">
        <svg width="22" height="10" style="vertical-align:middle;margin-right:4px">
          <line x1="0" y1="5" x2="22" y2="5" stroke="#8a9269" stroke-width="1.5" stroke-dasharray="4 3"/></svg>Inferred</span>
      <span class="topo-legend-item">
        <span style="color:#fbbf24;font-size:12px;margin-right:4px">&#9888;</span>Has issues</span>
    </div>"""

    topology_html = f"""
    <section id="network-topology" class="report-section">
      <h2>3. Network Topology</h2>
      <p>Hierarchical diagrams for each managed site showing upstream and downstream packet
         flow from the internet edge through MX security appliances into the switching
         fabric. The diagram renders appliances and switches as the primary tree, while
         wireless and other edge devices are summarized inside their parent switch counts.
         Switch cards display approximate front-panel port layouts, uplink ports, upstream
         neighbors, and child device counts. Solid edges indicate LLDP/CDP-confirmed
         adjacencies; dashed edges indicate the internet handoff above root devices.</p>
      {topo_legend}
      {"".join(topo_site_parts) if topo_site_parts else
       '<div class="summary-card"><div class="summary-body">No site topology data available.</div></div>'}
    </section>
    """

    # =========================================================
    # SECTION 4: TRAFFIC FLOWS & BOTTLENECK ANALYSIS
    # =========================================================
    def _speed_num(s) -> int | None:
        try:
            return int(str(s).split()[0])
        except (ValueError, IndexError):
            return None

    # Flatten RF profiles to net_id -> first profile (for band-steering / width context)
    _rf_by_net: Dict[str, Dict] = {}
    if isinstance(rf_profiles, dict):
        for _nid, _profs in rf_profiles.items():
            if isinstance(_profs, list) and _profs:
                _rf_by_net[_nid] = _profs[0]

    traffic_sections_html = []

    for net_id, net_data in sorted(
        devices_by_network.items(), key=lambda x: x[1]["name"]
    ):
        net_name = net_data["name"]
        nd = net_data["devices"]
        appliances = [d for d in nd if d.get("productType") == "appliance"]
        switches   = [d for d in nd if d.get("productType") == "switch"]
        aps        = [d for d in nd if d.get("productType") == "wireless"]

        sec = f'<div class="building-section">'
        sec += f"<h3>{net_name}</h3>"

        # ── Path summary bar ───────────────────────────────────────────────
        _path_parts = []
        if appliances:
            _mx = appliances[0]
            _mx_name = _mx.get("name") or _mx.get("model") or _mx.get("serial", "MX")
            _mx_status = _mx.get("status", "unknown")
            _mx_cls = "badge-ok" if _mx_status == "online" else "badge-fail"
            _path_parts.append(
                f'Internet &rarr; <span class="badge {_mx_cls}">{_he(_mx_name)}</span>'
            )
        else:
            _path_parts.append('Internet &rarr; <span class="badge badge-warn">[No MX]</span>')

        if switches:
            _sw_issues_total = sum(len(port_issues_by_switch.get(s.get("serial",""), [])) for s in switches)
            _sw_cls = "badge-fail" if _sw_issues_total > 5 else ("badge-warn" if _sw_issues_total else "badge-ok")
            _path_parts.append(
                f'<span class="badge {_sw_cls}">{len(switches)} Switch{"es" if len(switches)!=1 else ""}'
                f'{f" · {_sw_issues_total} issue(s)" if _sw_issues_total else ""}</span>'
            )
        if aps:
            _ap_high = sum(
                1 for a in aps
                if float((ap_util_by_serial.get(a.get("serial","")) or {}).get("utilizationTotal", 0)) > 70
            )
            _ap_cls = "badge-fail" if _ap_high else "badge-ok"
            _path_parts.append(
                f'<span class="badge {_ap_cls}">{len(aps)} AP{"s" if len(aps)!=1 else ""}'
                f'{f" · {_ap_high} high-util" if _ap_high else ""}</span>'
            )
        _path_parts.append("Clients")

        sec += (
            f'<p class="traffic-path">'
            f'{"&nbsp;&rarr;&nbsp;".join(_path_parts)}</p>'
        )

        # ── MX / WAN edge ─────────────────────────────────────────────────
        if appliances:
            sec += "<h4>WAN / Edge</h4>"
            for _mx in appliances:
                _serial  = _mx.get("serial", "")
                _name    = _mx.get("name") or _mx.get("model") or _serial
                _status  = _mx.get("status", "unknown")
                _model   = _mx.get("model", "")
                _s_cls   = "badge-ok" if _status == "online" else "badge-fail"

                # WAN uplink status for this appliance
                _uplinks = [
                    u for u in uplink_statuses
                    if isinstance(u, dict) and u.get("serial") == _serial
                ]
                _uplink_rows = ""
                _wan_issues = []
                for _ul in _uplinks:
                    for _iface in _ul.get("uplinks", []):
                        _iface_st = _iface.get("status", "unknown")
                        _iface_cls = "badge-ok" if _iface_st == "active" else "badge-fail"
                        _ip = _iface.get("ip") or "—"
                        _isp = _iface.get("provider") or _iface.get("publicIp") or "—"
                        _uplink_rows += (
                            f"<tr>"
                            f"<td>{_he(_iface.get('interface',''))}</td>"
                            f'<td><span class="badge {_iface_cls}">{_he(_iface_st)}</span></td>'
                            f"<td>{_he(_ip)}</td>"
                            f"<td>{_he(str(_isp))}</td>"
                            f"</tr>"
                        )
                        if _iface_st != "active":
                            _wan_issues.append(
                                f"WAN interface <strong>{_he(_iface.get('interface',''))}</strong> "
                                f"is <strong>{_he(_iface_st)}</strong> — failover or ISP outage"
                            )

                sec += '<div class="device-card">'
                sec += (
                    f'<div class="device-card-header">'
                    f"<strong>{_he(_name)}</strong>"
                    f' <code class="serial">{_serial}</code>'
                    f' <span class="badge">{_he(_model)}</span>'
                    f' <span class="badge {_s_cls}">{_status}</span>'
                    f"</div>"
                )
                if _uplink_rows:
                    sec += (
                        '<table class="data dense" style="margin-top:6px">'
                        "<thead><tr><th>Interface</th><th>Status</th><th>IP</th><th>ISP / Public IP</th></tr></thead>"
                        f"<tbody>{_uplink_rows}</tbody></table>"
                    )
                if _wan_issues:
                    sec += '<div class="bottleneck-list"><strong>WAN Issues:</strong><ul>'
                    for _b in _wan_issues:
                        sec += f"<li>{_b}</li>"
                    sec += "</ul></div>"
                elif _uplinks:
                    sec += '<div class="device-ok">&#10003; All WAN interfaces active.</div>'
                sec += "</div>"  # device-card

        # ── Per-switch + grouped AP analysis ──────────────────────────────
        if switches:
            sec += "<h4>Switches &amp; Connected APs</h4>"

            # Sort switches: ones with issues first, then by name
            def _sw_sort_key(sw):
                _s = sw.get("serial", "")
                _issues = len(port_issues_by_switch.get(_s, []))
                return (-_issues, (sw.get("name") or "").lower())

            for sw in sorted(switches, key=_sw_sort_key):
                serial   = sw.get("serial", "")
                sw_name  = sw.get("name") or sw.get("model") or serial
                sw_model = sw.get("model", "")
                sw_status = sw.get("status", "unknown")
                status_cls = "badge-ok" if sw_status == "online" else "badge-fail"
                poe_data = poe_by_serial.get(serial, {})
                poe_watts = float(poe_data.get("avgWatts", 0)) if poe_data else 0.0
                sw_issues = port_issues_by_switch.get(serial, [])
                sw_ap_serials = switch_to_aps.get(serial, [])
                sw_ap_devices = [
                    a for a in aps if a.get("serial") in set(sw_ap_serials)
                ]

                sec += '<div class="device-card">'
                sec += (
                    f'<div class="device-card-header">'
                    f"<strong>{_he(sw_name)}</strong>"
                    f' <code class="serial">{serial}</code>'
                    f' <span class="badge">{_he(sw_model)}</span>'
                    f' <span class="badge {status_cls}">{sw_status}</span>'
                )
                if poe_watts > 0:
                    _poe_cls = "badge-fail" if poe_watts > 100 else ("badge-warn" if poe_watts > 60 else "badge-info")
                    sec += f' <span class="badge {_poe_cls}">PoE {poe_watts:.0f} W avg</span>'
                if sw_ap_devices:
                    sec += f' <span class="badge badge-info">{len(sw_ap_devices)} AP{"s" if len(sw_ap_devices)!=1 else ""} connected</span>'
                sec += "</div>"

                # Port issues table (compact)
                if sw_issues:
                    sec += (
                        f'<div class="device-issues">'
                        f"<strong>&#9888; {len(sw_issues)} port issue(s):</strong>"
                        f'<table class="data dense" style="margin-top:4px">'
                        f"<thead><tr><th>Port</th><th>Speed</th><th>Duplex</th><th>Errors</th><th>Uplink</th></tr></thead><tbody>"
                    )
                    for _issue in sw_issues[:10]:
                        _err_str = ", ".join(_issue["errors"]) if _issue["errors"] else "—"
                        _ul_flag = "Yes" if _issue.get("isUplink") else ""
                        sec += (
                            f"<tr>"
                            f"<td><strong>{_he(str(_issue['port']))}</strong></td>"
                            f"<td>{_he(str(_issue['speed']))}</td>"
                            f"<td>{_he(str(_issue['duplex']))}</td>"
                            f"<td>{_he(_err_str)}</td>"
                            f"<td>{_ul_flag}</td>"
                            f"</tr>"
                        )
                    if len(sw_issues) > 10:
                        sec += f"<tr><td colspan='5'>&hellip; +{len(sw_issues)-10} more</td></tr>"
                    sec += "</tbody></table></div>"

                # Bottleneck bullets
                _bottlenecks = []
                _low_spd = [i for i in sw_issues if i.get("isUplink") and _speed_num(i.get("speed")) in [10, 100]]
                _err_ports = [i for i in sw_issues if i.get("error_count", 0) > 0]
                if _low_spd:
                    _bottlenecks.append(
                        f"{len(_low_spd)} uplink port(s) below 1 Gbps &mdash; "
                        "review cabling, SFP, or ISP handoff"
                    )
                if _err_ports:
                    _bottlenecks.append(
                        f"{len(_err_ports)} port(s) with frame errors &mdash; "
                        "cable degradation, bad SFP, or transceiver mismatch"
                    )
                if poe_watts > 100:
                    _bottlenecks.append(
                        f"Very high PoE draw ({poe_watts:.0f} W avg) &mdash; "
                        "verify remaining budget; power-limited ports drop connected APs"
                    )
                elif poe_watts > 60:
                    _bottlenecks.append(
                        f"Elevated PoE draw ({poe_watts:.0f} W avg) &mdash; "
                        "monitor budget if additional PoE devices are planned"
                    )
                if _bottlenecks:
                    sec += '<div class="bottleneck-list"><strong>Bottlenecks / Concerns:</strong><ul>'
                    for _b in _bottlenecks:
                        sec += f"<li>{_b}</li>"
                    sec += "</ul></div>"
                elif not sw_issues:
                    sec += '<div class="device-ok">&#10003; No port issues detected.</div>'

                # ── APs connected to this switch ───────────────────────
                if sw_ap_devices:
                    sec += (
                        '<div class="ap-under-switch">'
                        '<table class="data dense">'
                        "<thead><tr>"
                        "<th>AP</th><th>Model</th><th>Status</th>"
                        "<th>Ch Util</th><th>Tx%</th><th>Non-802.11%</th>"
                        "<th>Assoc fails</th><th>Auth fails</th><th>Notes</th>"
                        "</tr></thead><tbody>"
                    )
                    for _ap in sorted(sw_ap_devices, key=lambda a: (a.get("name") or "").lower()):
                        _ap_serial  = _ap.get("serial", "")
                        _ap_name    = _ap.get("name") or _ap.get("model") or _ap_serial
                        _ap_model   = _ap.get("model", "")
                        _ap_status  = _ap.get("status", "unknown")
                        _ap_s_cls   = "badge-ok" if _ap_status == "online" else "badge-fail"
                        _util       = ap_util_by_serial.get(_ap_serial) or {}
                        _tot_util   = float(_util.get("utilizationTotal", 0))
                        _tx_util    = float(_util.get("utilization80211Tx", 0))
                        _non80211   = float(_util.get("utilizationNon80211", 0))
                        _conn       = ap_conn_stats.get(_ap_serial) or {}
                        _assoc_fail = int(_conn.get("assoc", 0))
                        _auth_fail  = int(_conn.get("auth", 0))
                        _success    = int(_conn.get("success", 0))
                        _total_att  = _assoc_fail + _auth_fail + _success
                        _fail_rate  = round(100 * (_assoc_fail + _auth_fail) / _total_att) if _total_att else 0

                        _util_cls = (
                            "badge-fail" if _tot_util > 70
                            else "badge-warn" if _tot_util > 30
                            else "badge-ok"
                        )
                        _ap_notes = []
                        if _ap_status != "online":
                            _ap_notes.append(f'<span class="badge badge-fail">{_ap_status}</span>')
                        if _tot_util > 70:
                            _ap_notes.append('<span class="badge badge-fail">High util</span>')
                        if _non80211 > 20:
                            _ap_notes.append(f'<span class="badge badge-warn">Interference {_non80211:.0f}%</span>')
                        if _fail_rate > 15:
                            _ap_notes.append(f'<span class="badge badge-warn">Fail rate {_fail_rate}%</span>')

                        sec += (
                            f"<tr>"
                            f"<td>{_he(_ap_name)}</td>"
                            f"<td><code>{_he(_ap_model)}</code></td>"
                            f'<td><span class="badge {_ap_s_cls}">{_ap_status}</span></td>'
                            f'<td><span class="badge {_util_cls}">{_tot_util:.0f}%</span></td>'
                            f"<td>{_tx_util:.0f}%</td>"
                            f"<td>{_non80211:.0f}%</td>"
                            f"<td>{_assoc_fail if _assoc_fail else '—'}</td>"
                            f"<td>{_auth_fail if _auth_fail else '—'}</td>"
                            f"<td>{''.join(_ap_notes) if _ap_notes else '&#10003;'}</td>"
                            f"</tr>"
                        )
                    sec += "</tbody></table></div>"

                sec += "</div>"  # device-card

        # ── APs not mapped to a switch (no LLDP entry) ────────────────────
        _unmapped_aps = [a for a in aps if a.get("serial") not in ap_to_switch]
        if _unmapped_aps:
            sec += "<h4>Access Points (no switch LLDP mapping)</h4>"
            sec += (
                '<table class="data dense">'
                "<thead><tr>"
                "<th>AP</th><th>Model</th><th>Status</th>"
                "<th>Ch Util</th><th>Tx%</th><th>Non-802.11%</th>"
                "<th>Assoc fails</th><th>Auth fails</th>"
                "</tr></thead><tbody>"
            )
            for _ap in sorted(_unmapped_aps, key=lambda a: (a.get("name") or "").lower()):
                _s   = _ap.get("serial", "")
                _nm  = _ap.get("name") or _ap.get("model") or _s
                _mod = _ap.get("model", "")
                _st  = _ap.get("status", "unknown")
                _util = ap_util_by_serial.get(_s) or {}
                _tu  = float(_util.get("utilizationTotal", 0))
                _tx  = float(_util.get("utilization80211Tx", 0))
                _n80 = float(_util.get("utilizationNon80211", 0))
                _conn = ap_conn_stats.get(_s) or {}
                _af  = int(_conn.get("assoc", 0))
                _auf = int(_conn.get("auth", 0))
                _ucls = "badge-fail" if _tu > 70 else ("badge-warn" if _tu > 30 else "badge-ok")
                _scls = "badge-ok" if _st == "online" else "badge-fail"
                sec += (
                    f"<tr>"
                    f"<td>{_he(_nm)}</td>"
                    f"<td><code>{_he(_mod)}</code></td>"
                    f'<td><span class="badge {_scls}">{_st}</span></td>'
                    f'<td><span class="badge {_ucls}">{_tu:.0f}%</span></td>'
                    f"<td>{_tx:.0f}%</td><td>{_n80:.0f}%</td>"
                    f"<td>{_af if _af else '—'}</td><td>{_auf if _auf else '—'}</td>"
                    f"</tr>"
                )
            sec += "</tbody></table>"

        # ── RF profile summary for this network ───────────────────────────
        _rf = _rf_by_net.get(net_id)
        if _rf:
            _band_op   = (_rf.get("apBandSettings") or {}).get("bandOperationMode", "")
            _band_steer = (_rf.get("apBandSettings") or {}).get("bandSteeringEnabled", False)
            _client_bal = _rf.get("clientBalancingEnabled", False)
            _5g         = _rf.get("fiveGhzSettings") or {}
            _5g_width   = _5g.get("channelWidth", "auto")
            _5g_maxpwr  = _5g.get("maxPower")
            _rf_notes   = []
            if not _band_steer:
                _rf_notes.append("Band steering is <strong>disabled</strong> — clients may prefer 2.4 GHz, increasing congestion on that band")
            if not _client_bal:
                _rf_notes.append("Client load balancing is <strong>disabled</strong> — uneven client distribution across APs is more likely")
            if _5g_width not in ("auto", "80", "40"):
                _rf_notes.append(f"5 GHz channel width is set to <strong>{_5g_width}</strong> — verify this is intentional for the environment density")

            sec += (
                '<div class="summary-card" style="margin-top:12px">'
                '<div class="summary-title">RF Profile (first profile in network)</div>'
                '<div class="summary-body">'
                f"Band mode: <strong>{_he(_band_op or '—')}</strong> &nbsp;|&nbsp; "
                f"Band steering: <strong>{'On' if _band_steer else 'Off'}</strong> &nbsp;|&nbsp; "
                f"Client balancing: <strong>{'On' if _client_bal else 'Off'}</strong> &nbsp;|&nbsp; "
                f"5 GHz width: <strong>{_he(str(_5g_width))}</strong>"
                + (f" &nbsp;|&nbsp; Max power: <strong>{_5g_maxpwr} dBm</strong>" if _5g_maxpwr else "")
            )
            if _rf_notes:
                sec += "<ul style='margin-top:6px'>" + "".join(f"<li>{n}</li>" for n in _rf_notes) + "</ul>"
            sec += "</div></div>"

        sec += "</div>"  # building-section
        traffic_sections_html.append(sec)

    traffic_html = f"""
    <section id="traffic-flows" class="report-section">
      <h2>4. Traffic Flows &amp; Bottleneck Analysis</h2>
      <p>Each site shows the traffic path from WAN edge (MX) through switching and wireless
         layers to end clients. Switches include their connected APs with RF utilization and
         connection failure signals. Bottlenecks are called out at each layer.</p>
      {"".join(traffic_sections_html) if traffic_sections_html else '<div class="summary-card"><div class="summary-body">No device or uplink data was available for this organization at the time of backup. Run a full backup to populate this section.</div></div>'}
    </section>
    """

    # =========================================================
    # SECTION 4: DEVICE HEALTH & ISSUES
    # =========================================================
    issues_html = """
    <section id="device-health" class="report-section">
      <h2>5. Device Health &amp; Issues</h2>
    """

    if device_status_counts:
        issues_html += render_section(
            "Device Status Summary",
            [[status.title(), str(count)] for status, count in device_status_counts.items()],
        )

    if switch_port_issues:
        issues_html += """
        <h3>Switch Port Issues</h3>
        <table class="data">
          <thead>
            <tr>
              <th>Switch Serial</th><th>Port</th><th>Errors</th>
              <th>Speed</th><th>Duplex</th><th>PoE Mode</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
        """
        for issue in switch_port_issues[:25]:
            err_display = ", ".join(issue["errors"]) if issue["errors"] else "—"
            issues_html += (
                f"<tr>"
                f"<td>{issue['switch']}</td>"
                f"<td>{issue['port']}</td>"
                f"<td>{err_display}</td>"
                f"<td>{issue['speed']}</td>"
                f"<td>{issue['duplex']}</td>"
                f"<td>{issue['poeMode']}</td>"
                f"<td>{issue['status']}</td>"
                f"</tr>"
            )
        issues_html += "</tbody></table>"

    if config_issues:
        issues_html += """
        <h3>Configuration Issues</h3>
        <table class="data">
          <thead>
            <tr><th>Switch Serial</th><th>Port</th><th>Issue</th><th>Type</th></tr>
          </thead>
          <tbody>
        """
        for issue in config_issues[:15]:
            issues_html += (
                f"<tr>"
                f"<td>{issue['switch']}</td>"
                f"<td>{issue['port']}</td>"
                f"<td>{issue['issue']}</td>"
                f"<td>{issue['type']}</td>"
                f"</tr>"
            )
        issues_html += "</tbody></table>"

    if high_util_devices:
        issues_html += """
        <h3>High Utilization Access Points (&gt;70%)</h3>
        <table class="data">
          <thead>
            <tr>
              <th>AP Serial</th><th>Total Util</th>
              <th>Non-802.11</th><th>Tx</th><th>Rx</th>
            </tr>
          </thead>
          <tbody>
        """
        for device in high_util_devices[:20]:
            issues_html += (
                f"<tr>"
                f"<td>{device.get('serial', 'Unknown')}</td>"
                f"<td>{float(device.get('utilizationTotal', 0)):.1f}%</td>"
                f"<td>{float(device.get('utilizationNon80211', 0)):.1f}%</td>"
                f"<td>{float(device.get('utilization80211Tx', 0)):.1f}%</td>"
                f"<td>{float(device.get('utilization80211Rx', 0)):.1f}%</td>"
                f"</tr>"
            )
        issues_html += "</tbody></table>"

    # Wireless configuration snapshot (settings + SSIDs + mesh)
    if wireless_settings or wireless_ssids or wireless_mesh_statuses:
        issues_html += "<h3>Wireless Configuration Snapshot</h3>"

        if isinstance(wireless_settings, dict) and wireless_settings:
            issues_html += """
            <table class="data">
              <thead>
                <tr>
                  <th>Network</th><th>Meshing</th><th>Upgrade Strategy</th>
                  <th>Multicast→Unicast</th><th>Location Analytics</th><th>Reg Domain</th>
                </tr>
              </thead>
              <tbody>
            """
            for net_id, settings in wireless_settings.items():
                if not isinstance(settings, dict):
                    continue
                reg = settings.get("regulatoryDomain") or {}
                issues_html += (
                    "<tr>"
                    f"<td>{_he(network_names.get(net_id, net_id))}</td>"
                    f"<td>{'On' if settings.get('meshingEnabled') else 'Off'}</td>"
                    f"<td>{_he(str(settings.get('upgradeStrategy') or '—'))}</td>"
                    f"<td>{'On' if (settings.get('multicastToUnicastConversion') or {}).get('enabled') else 'Off'}</td>"
                    f"<td>{'On' if settings.get('locationAnalyticsEnabled') else 'Off'}</td>"
                    f"<td>{_he(reg.get('countryCode') or reg.get('name') or '—')}</td>"
                    "</tr>"
                )
            issues_html += "</tbody></table>"

        if isinstance(wireless_ssids, dict) and wireless_ssids:
            issues_html += """
            <div class="summary-card">
              <div class="summary-title">SSID Configuration (redacted)</div>
              <div class="summary-body">
                The report shows SSID configuration status but does not expose passphrases.
              </div>
            </div>
            """
            for net_id, ssids in wireless_ssids.items():
                if not isinstance(ssids, list):
                    continue
                issues_html += f"<h4>{_he(network_names.get(net_id, net_id))}</h4>"
                issues_html += """
                <table class="data">
                  <thead>
                    <tr>
                      <th>SSID</th><th>Enabled</th><th>Auth</th><th>Encryption</th>
                      <th>Band</th><th>Min Bitrate</th><th>Visible</th><th>VLAN</th>
                    </tr>
                  </thead>
                  <tbody>
                """
                for ssid in ssids[:20]:
                    if not isinstance(ssid, dict):
                        continue
                    ssid_label = ssid.get("name") or f"SSID {ssid.get('number', '')}"
                    issues_html += (
                        "<tr>"
                        f"<td>{_he(ssid_label)}</td>"
                        f"<td>{'Yes' if ssid.get('enabled') else 'No'}</td>"
                        f"<td>{_he(ssid.get('authMode') or '—')}</td>"
                        f"<td>{_he(ssid.get('wpaEncryptionMode') or ssid.get('encryptionMode') or '—')}</td>"
                        f"<td>{_he(ssid.get('bandSelection') or '—')}</td>"
                        f"<td>{_he(str(ssid.get('minBitrate') or '—'))}</td>"
                        f"<td>{'Yes' if ssid.get('visible', True) else 'No'}</td>"
                        f"<td>{'Yes' if ssid.get('useVlanTagging') else 'No'}</td>"
                        "</tr>"
                    )
                issues_html += "</tbody></table>"

        if isinstance(wireless_mesh_statuses, dict) and wireless_mesh_statuses:
            mesh_notes = []
            for net_id, payload in wireless_mesh_statuses.items():
                if isinstance(payload, dict) and payload.get("error"):
                    mesh_notes.append(f"{network_names.get(net_id, net_id)}: {payload.get('error')}")
            if mesh_notes:
                issues_html += (
                    '<div class="summary-card">'
                    '<div class="summary-title">Mesh Status Notes</div>'
                    '<div class="summary-body">'
                    + "<br>".join(_he(n) for n in mesh_notes[:6])
                    + "</div></div>"
                )

    # Firmware upgrade history summary
    if isinstance(firmware_upgrades, list) and firmware_upgrades:
        fw_rows = []
        fw_items = []
        for item in firmware_upgrades:
            if not isinstance(item, dict):
                continue
            dt = _parse_dt(item.get("time", ""))
            if not dt and item.get("completedAt"):
                try:
                    dt = datetime.strptime(item["completedAt"], "%Y-%m-%d %H:%M:%S UTC")
                except ValueError:
                    dt = None
            fw_items.append((dt, item))
        fw_items.sort(key=lambda x: x[0] or datetime.min, reverse=True)
        for dt, item in fw_items[:12]:
            net = (item.get("network") or {}).get("name") or (item.get("network") or {}).get("id", "—")
            to_ver = (item.get("toVersion") or {}).get("shortName") or (item.get("toVersion") or {}).get("firmware", "—")
            from_ver = (item.get("fromVersion") or {}).get("shortName") or (item.get("fromVersion") or {}).get("firmware", "—")
            fw_rows.append([
                net,
                str(item.get("productTypes") or "—"),
                str(item.get("status") or "—"),
                from_ver,
                to_ver,
                dt.strftime("%Y-%m-%d") if dt else "—",
            ])
        issues_html += render_section(
            "Recent Firmware Upgrades (last 12)",
            [["Network", "Product", "Status", "From", "To", "Date"]] + fw_rows if fw_rows else [],
        )

    if eox_devices:
        issues_html += render_section(
            "End-of-Life / End-of-Support Inventory",
            [["Device", "Model", "Network", "Status", "End of Sale", "End of Support"]]
            + [[
                d.get("name", "—"),
                d.get("model", "—"),
                d.get("network", "—"),
                d.get("status", "—"),
                d.get("endOfSale", "—"),
                d.get("endOfSupport", "—"),
            ] for d in eox_devices[:20]],
        )

    # Alerts summary
    if isinstance(alerts_history, dict) and alerts_history:
        from collections import Counter
        alert_items = []
        for net_id, alerts in alerts_history.items():
            if not isinstance(alerts, list):
                continue
            for alert in alerts:
                if not isinstance(alert, dict):
                    continue
                dt = _parse_dt(alert.get("occurredAt", ""))
                alert_items.append({
                    "dt": dt,
                    "type": alert.get("alertType") or alert.get("alertTypeId") or "Alert",
                    "device": (alert.get("device") or {}).get("name") or (alert.get("device") or {}).get("serial") or "—",
                    "network": network_names.get(net_id, net_id),
                })
        alert_items.sort(key=lambda x: x["dt"] or datetime.min, reverse=True)
        recent = [a for a in alert_items if a["dt"] and a["dt"] >= datetime.now(tz=a["dt"].tzinfo) - timedelta(days=30)]
        counts = Counter([a["type"] for a in recent])
        if counts:
            issues_html += render_section(
                "Alerts (last 30 days)",
                [["Alert Type", "Count"]] + [[t, str(c)] for t, c in counts.most_common(8)],
            )
        if alert_items:
            issues_html += render_section(
                "Most Recent Alerts",
                [["Date", "Network", "Device", "Alert"]]
                + [[
                    a["dt"].strftime("%Y-%m-%d %H:%M") if a["dt"] else "—",
                    a["network"],
                    a["device"],
                    a["type"],
                ] for a in alert_items[:10]],
            )

    if not switch_port_issues and not config_issues and not high_util_devices:
        issues_html += (
            '<div class="summary-card">'
            '<div class="summary-body">No significant issues detected in the current data snapshot.</div>'
            "</div>"
        )

    issues_html += "</section>"

    # =========================================================
    # SECTION 5: PoE POWER ANALYSIS
    # =========================================================
    poe_html = """
    <section id="poe-analysis" class="report-section">
      <h2>6. PoE Power Analysis</h2>
    """
    if poe_switches:
        poe_html += render_section(
            "PoE Consumption by Switch (24 h average)",
            [
                [
                    s.get("serial", ""),
                    f"{float(s.get('avgWatts', 0)):.1f} W",
                    f"{float(s.get('powerUsageInWh', 0)):.1f} Wh",
                ]
                for s in poe_switches[:20]
            ],
        )
    if poe_ports:
        poe_html += render_section(
            "Top PoE Ports by Energy (24 h)",
            [
                [
                    p.get("serial", ""),
                    p.get("portId", ""),
                    f"{float(p.get('powerUsageInWh', 0)):.1f} Wh",
                ]
                for p in poe_ports[:20]
            ],
        )
    if not poe_switches and not poe_ports:
        poe_html += (
            '<div class="summary-card">'
            '<div class="summary-body">No PoE data available in this backup.</div>'
            "</div>"
        )
    poe_html += "</section>"

    # =========================================================
    # SECTION 6: SECURITY & COMPLIANCE
    # =========================================================
    # Build category-level summary from baseline checks
    _sec_by_cat: dict[str, list] = {}
    for _chk in security_checks:
        _cat = _chk.get("check", "Other")
        _sec_by_cat.setdefault(_cat, []).append(_chk)

    # Per-category pass/fail summary rows
    _sec_cat_rows = ""
    for _cat, _items in sorted(_sec_by_cat.items()):
        _cat_pass = sum(1 for c in _items if c.get("status", "").lower() == "pass")
        _cat_fail = sum(1 for c in _items if c.get("status", "").lower() == "fail")
        _cat_warn = sum(1 for c in _items if c.get("status", "").lower() == "warning")
        if _cat_fail:
            _cat_cls = "check-fail"
        elif _cat_warn:
            _cat_cls = "check-warning"
        else:
            _cat_cls = "check-pass"
        _net_names = ", ".join(sorted({c.get("networkName", "Org") for c in _items}))
        _detail = _items[0].get("description", "") if len(_items) == 1 else f"{len(_items)} networks evaluated"
        _sec_cat_rows += (
            f"<tr>"
            f"<td><strong>{_he(_cat)}</strong></td>"
            f'<td class="{_cat_cls}">'
            f'{"❌ Fail" if _cat_fail else ("⚠ Warning" if _cat_warn else "✔ Pass")}'
            f"</td>"
            f"<td>{_cat_pass + _cat_warn + _cat_fail} sites</td>"
            f"<td>{_he(_detail)}</td>"
            f"</tr>"
        )

    # Port forwarding posture summary
    _pf_checks = [c for c in security_checks if "port forwarding" in c.get("check", "").lower()]
    _pf_exposed = [c for c in _pf_checks if c.get("status", "").lower() != "pass"]
    if _pf_exposed:
        _pf_note = (
            f"<strong class='text-crit'>{len(_pf_exposed)} site(s) have internet-exposed port "
            f"forwarding rules that may require review.</strong> Each exposed rule creates a "
            "direct inbound path from the internet to an internal host. Confirm all forwarding "
            "rules are intentional, access-controlled, and documented."
        )
    elif _pf_checks:
        _pf_note = (
            f"No unrestricted internet-exposed port forwarding rules were detected across "
            f"{len(_pf_checks)} site(s) evaluated. Continue to review forwarding rules "
            "periodically as application requirements change."
        )
    else:
        _pf_note = (
            "Port forwarding posture could not be evaluated — appliance baseline data not "
            "available in this backup set."
        )

    # Overall security posture framing
    _sec_summary = security_baseline.get("summary", {}) if isinstance(security_baseline, dict) else {}
    _sec_total = sum(_sec_summary.values()) if _sec_summary else len(security_checks)
    if _sec_fail > 0:
        _sec_posture = (
            f"<strong class='text-crit'>Action required</strong> — {_sec_fail} check(s) failing "
            f"out of {_sec_total} evaluated. Failing checks represent active exposure that should "
            "be addressed before the next maintenance window."
        )
    elif _sec_warn > 0:
        _sec_posture = (
            f"<strong>Attention advised</strong> — {_sec_warn} check(s) in warning state. "
            "No critical failures detected, but warning-level gaps should be scheduled for "
            "remediation to harden posture proactively."
        )
    else:
        _sec_posture = (
            f"<strong>Posture is satisfactory</strong> — {_sec_pass} check(s) passed with no "
            "failures or warnings detected. Maintain current configuration discipline and "
            "review this section after any major firmware or policy change."
        )

    security_html = f"""
    <section id="security-baseline" class="report-section">
      <h2>7. Security &amp; Compliance</h2>
      <p>This section evaluates security posture from two angles: an appliance-level baseline
         check (AMP, IDS/IPS, spoof protection, and internet exposure) and a CIS Controls
         mapping in the following section. Together they form the security health layer of
         this network audit.</p>

      <div class="summary-card">
        <div class="summary-title">Security Posture Summary</div>
        <div class="summary-body">
          {_sec_posture}
          <br><br>
          <strong>Firewall &amp; Internet Exposure:</strong> {_pf_note}
          <br><br>
          <em>Note: L3 inbound firewall rule detail requires a separate collection step
          (<code>GET /networks/&#123;id&#125;/appliance/firewall/inboundFirewallRules</code>).
          That data is not present in this backup set. Add it to the pipeline to surface
          specific rule-level exposure in future reports.</em>
        </div>
      </div>

      <table class="data">
        <thead>
          <tr>
            <th>Check Category</th>
            <th>Result</th>
            <th>Scope</th>
            <th>Finding</th>
          </tr>
        </thead>
        <tbody>{_sec_cat_rows}</tbody>
      </table>

      {render_security_baseline(security_checks)}
    </section>
    """

    # Profile optimization notes (first RF profile per network)
    rf_profile_notes = []
    if isinstance(rf_profiles, dict):
        for net_id, profiles in rf_profiles.items():
            if not isinstance(profiles, list) or not profiles:
                continue
            prof = profiles[0] if isinstance(profiles[0], dict) else None
            if not prof:
                continue
            notes = []
            ap_band = prof.get("apBandSettings") or {}
            if ap_band.get("bandSteeringEnabled") is False:
                notes.append("band steering disabled")
            min_24 = (prof.get("twoFourGhzSettings") or {}).get("minBitrate")
            if isinstance(min_24, (int, float)) and min_24 <= 11:
                notes.append("2.4 GHz min bitrate ≤ 11 Mbps")
            min_5 = (prof.get("fiveGhzSettings") or {}).get("minBitrate")
            if isinstance(min_5, (int, float)) and min_5 <= 12:
                notes.append("5 GHz min bitrate ≤ 12 Mbps")
            width_5 = (prof.get("fiveGhzSettings") or {}).get("channelWidth")
            if str(width_5).strip() in ("20", "20MHz"):
                notes.append("5 GHz channel width fixed at 20 MHz")
            if notes:
                rf_profile_notes.append(f"{network_names.get(net_id, net_id)}: " + "; ".join(notes))

    # =========================================================
    # SECTION 7: RECOMMENDATIONS & IMPLEMENTATION PLAN
    # =========================================================
    rec_html = md_to_html(rec_md)
    extra_recs_html = ""
    if mixed_lifecycle_nets or rf_profile_notes:
        extra_recs_html = (
            "<h3>Hardware Mix &amp; AP Profile Optimization</h3>"
            "<div class=\"summary-card\"><div class=\"summary-body\">"
        )
        if mixed_lifecycle_nets:
            extra_recs_html += (
                "<strong>Mixed hardware generations detected:</strong> "
                + _he(", ".join(sorted(mixed_lifecycle_nets)[:8]))
                + ". Align refresh cycles so upstream switching and downstream APs are on compatible lifecycle tiers."
                "<br><br>"
            )
        if rf_profile_notes:
            extra_recs_html += (
                "<strong>AP profile optimization opportunities:</strong><br>"
                + "<br>".join(_he(n) for n in rf_profile_notes[:10])
                + "<br>Consider raising minimum bitrates, enabling band steering, and widening 5 GHz channels where density allows."
            )
        extra_recs_html += "</div></div>"

    recommendations_html = f"""
    <section id="recommendations" class="report-section">
      <h2>8. Recommendations &amp; Implementation Plan</h2>
      {rec_html}
      {extra_recs_html}
      <h3>Prioritized Action Timeline</h3>
      <div class="summary-card">
        <div class="summary-body">
          <ol>
            <li><strong>Immediate (0&ndash;2 weeks):</strong>
                Resolve port frame errors, duplex mismatches, and sub-gigabit port speeds.
                Investigate any offline devices.</li>
            <li><strong>Short-term (2&ndash;6 weeks):</strong>
                Address high-utilization APs &mdash; adjust channels, reduce SSID count,
                add APs, or relocate to improve coverage distribution.</li>
            <li><strong>Medium-term (6&ndash;12 weeks):</strong>
                Audit PoE budgets on high-draw switches. Plan upgrades if budget is
                within 20% of capacity. Standardize port configurations.</li>
            <li><strong>Long-term (3&ndash;6 months):</strong>
                Evaluate hardware refresh for devices approaching end-of-life.
                Implement network segmentation and 802.1X port authentication.</li>
          </ol>
        </div>
      </div>
    </section>
    """

    # =========================================================
    # SECTION 8: CIS 8 CONTROLS ASSESSMENT
    # =========================================================
    cis8_checks = [
        ("CIS 1 — Inventory & Control of Enterprise Assets",
         "Partial",
         f"{total_devices} devices tracked via Meraki Dashboard. Ensure all unmanaged "
         "assets are also inventoried in a CMDB or equivalent."),
        ("CIS 2 — Inventory & Control of Software Assets",
         "Info",
         "Software inventory is outside the scope of Meraki telemetry. "
         "Integrate with endpoint management (MDM/EDR) to close this gap."),
        ("CIS 3 — Data Protection",
         "Info",
         "Meraki provides VLAN segmentation and content filtering. Confirm data-at-rest "
         "encryption and DLP policies are in place at the endpoint and cloud layers."),
        ("CIS 4 — Secure Configuration of Enterprise Assets",
         "Partial" if switch_port_issues or config_issues else "Pass",
         f"{len(switch_port_issues)} port issue(s) and {len(config_issues)} config "
         "anomaly(ies) detected. Harden switch port configurations and review default "
         "VLAN assignments."),
        ("CIS 5 — Account Management",
         "Info",
         "Meraki Dashboard SSO/SAML integration should be enforced. Review Dashboard "
         "admin roles and remove stale accounts."),
        ("CIS 6 — Access Control Management",
         "Partial",
         "VLAN-based segmentation is in use. Evaluate 802.1X port authentication "
         "and Group Policy enforcement for granular access control."),
        ("CIS 7 — Continuous Vulnerability Management",
         "Warning",
         "Meraki auto-firmware updates should be enabled. Confirm devices are not "
         "running EOL firmware versions and that update windows are configured."),
        ("CIS 9 — Email & Web Browser Protections",
         "Info",
         "Meraki MX content filtering and threat protection can address parts of this "
         "control. Verify AMP and IDS/IPS are enabled on MX appliances."),
        ("CIS 12 — Network Infrastructure Management",
         "Partial" if switch_port_issues else "Pass",
         "Network topology is centrally managed. Review port configurations and ensure "
         "management VLAN is isolated from user traffic."),
        ("CIS 13 — Network Monitoring & Defense",
         "Warning",
         "Meraki Dashboard provides basic alerting. Integrate syslog with a SIEM for "
         "centralized event correlation and anomaly detection."),
    ]
    cis8_rows = "".join(
        f"<tr>"
        f"<td>{c[0]}</td>"
        f'<td><span class="check-{"pass" if c[1] == "Pass" else "warning" if c[1] in ("Partial","Warning") else "unknown"}">{c[1]}</span></td>'
        f"<td>{c[2]}</td>"
        f"</tr>"
        for c in cis8_checks
    )
    cis8_html = f"""
    <section id="cis8" class="report-section">
      <h2>9. CIS 8 Controls Assessment</h2>
      <p>The following table maps observable Meraki network data to relevant CIS Controls v8
         sub-controls. Items marked <em>Info</em> require data from systems outside the Meraki
         platform to fully evaluate.</p>
      <table class="data">
        <thead>
          <tr>
            <th>CIS Control</th>
            <th>Status</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>{cis8_rows}</tbody>
      </table>
    </section>
    """

    # =========================================================
    # SECTION 9: LICENSING SUMMARY
    # =========================================================
    licensing_mode = (
        licensing_data.get("licenseMode")
        if isinstance(licensing_data, dict)
        else None
    )
    org_license_paths = [os.path.join(path, "licensing.json") for path in org_dirs]
    org_license_payloads = [load_json(path) for path in org_license_paths]
    coverage_total = len(org_license_payloads)
    coverage_ok = sum(
        1
        for payload in org_license_payloads
        if isinstance(payload, dict) and not payload.get("error")
    )

    # Build license rows — supports both co-term key lists and per-device status APIs.
    # co-term: licenses is a list of {key, expired (bool), counts, editions, startedAt, duration}
    # per-device: licenses is a list of {licenseType/productType, status (str), expirationDate, ...}
    _lic_rows_html = ""
    _raw_lic_list = (
        licensing_data.get("licenses", [])
        if isinstance(licensing_data, dict)
        else []
    )
    for _lic in _raw_lic_list:
        if not isinstance(_lic, dict):
            continue
        # Determine model / product from whichever fields are present
        _counts = _lic.get("counts") or []  # co-term: [{"count": N, "model": "MR Enterprise"}]
        _editions = _lic.get("editions") or []  # co-term: [{"edition": "Ent", "productType": "appliance"}]
        if _counts:
            _lic_type = ", ".join(
                f"{c.get('count', '?')}× {c.get('model', '?')}" for c in _counts
            )
        else:
            _lic_type = _lic.get("licenseType") or _lic.get("productType") or "—"
        # Status — co-term uses expired bool; per-device uses status string
        _is_expired = (
            _lic.get("expired") is True
            or str(_lic.get("status", "")).lower() in ("expired", "inactive")
        )
        _is_invalidated = bool(_lic.get("invalidated"))
        if _is_invalidated:
            _status_str = "Invalidated"
            _status_cls = "warning"
        elif _is_expired:
            _status_str = "Expired"
            _status_cls = "fail"
        else:
            _status_str = _lic.get("status") or "Active"
            _status_cls = "pass"
        # Expiry date — co-term calculates from startedAt + duration (days); per-device has expirationDate
        _exp_date = _lic.get("expirationDate") or _lic.get("expiration") or "—"
        if _exp_date == "—" and _lic.get("startedAt") and _lic.get("duration"):
            try:
                _started = datetime.fromisoformat(_lic["startedAt"].replace("Z", "+00:00"))
                _exp_dt = _started + timedelta(days=int(_lic["duration"]))
                _exp_date = _exp_dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        _key = _he(_lic.get("key") or "—")
        _lic_rows_html += (
            f"<tr>"
            f"<td><code style='font-size:9px'>{_key}</code></td>"
            f"<td>{_he(_lic_type)}</td>"
            f'<td><span class="check-{_status_cls}">{_status_str}</span></td>'
            f"<td>{_he(str(_exp_date))}</td>"
            f"</tr>"
        )

    _active_count = sum(
        1 for _l in _raw_lic_list
        if isinstance(_l, dict) and not _l.get("invalidated") and (
            _l.get("expired") is False
            or str(_l.get("status", "")).lower() in ("ok", "active", "in compliance")
        )
    )
    _expired_count = sum(
        1 for _l in _raw_lic_list
        if isinstance(_l, dict) and (
            _l.get("expired") is True
            or str(_l.get("status", "")).lower() in ("expired", "inactive")
        )
    )
    _total_lic = len(_raw_lic_list)

    if isinstance(licensing_data, dict) and licensing_data.get("error"):
        _lic_summary_note = (
            "<strong>Licensing data unavailable</strong> — the API returned an error for this "
            "organization. Verify that the API key has <em>read</em> access to the licensing "
            "endpoints and re-run the backup pipeline."
        )
    elif licensing_mode:
        _lic_summary_note = (
            f"This organization uses the <strong>{_he(licensing_mode)}</strong> licensing model. "
            f"Licensing is tracked at the organization level — {_total_lic} license key(s) on "
            f"record: <strong>{_active_count} active</strong>, "
            f"<strong class='{'text-crit' if _expired_count else ''}'>{_expired_count} expired</strong>. "
            "Meraki co-term licenses do not map 1:1 to individual devices or networks; Dashboard "
            "determines overall compliance from the combined pool of active seat counts."
        )
    else:
        _lic_summary_note = (
            "Licensing scope could not be determined from the collected payload. "
            "This section should be treated as partial until the pipeline is validated against "
            "the <code>/organizations/{id}/licenses/overview</code> endpoint."
        )

    if _lic_rows_html:
        _licensing_table = f"""
        <table class="data dense">
          <thead>
            <tr>
              <th>License Key</th>
              <th>Coverage (Model / Count)</th>
              <th>Status</th>
              <th>Expiration</th>
            </tr>
          </thead>
          <tbody>{_lic_rows_html}</tbody>
        </table>"""
    elif licensing_data is not None:
        # File was collected but the licenses array is empty (e.g. org uses a
        # different licensing mode or has no keys assigned yet)
        _licensing_table = """
        <div class="summary-card">
          <div class="summary-body">
            No license keys were returned by the Meraki API for this organization.
            If this organization uses <strong>per-device licensing</strong>, verify that
            <code>GET /organizations/{id}/licenses</code> is returning data and that the
            backup pipeline is storing it correctly. If the org is managed under an
            Enterprise Agreement or co-term umbrella, licensing may be tracked at the
            parent organization level.
          </div>
        </div>"""
    else:
        _licensing_table = """
        <div class="summary-card">
          <div class="summary-body">
            No <code>licensing.json</code> was found in this backup. Ensure the backup
            pipeline calls <code>GET /organizations/{id}/licenses</code> and stores the
            result before generating reports.
          </div>
        </div>"""

    licensing_html = f"""
    <section id="licensing" class="report-section">
      <h2>10. Licensing Summary</h2>
      <p>Cisco Meraki devices require active cloud-managed licenses to maintain Dashboard
         visibility and security feature enforcement. Expired licenses can cause devices to
         enter limited mode. Review expirations and plan renewals at least 90 days in advance.</p>
      <div class="summary-card">
        <div class="summary-title">Licensing Status &amp; Scope</div>
        <div class="summary-body">
          {_lic_summary_note}
          <br><br>
          Org backup coverage: <strong>{coverage_ok}/{coverage_total}</strong> org(s) with
          licensing data collected.
        </div>
      </div>
      {_licensing_table}
      <div class="summary-card">
        <div class="summary-title">Licensing Best Practices</div>
        <div class="summary-body">
          <ul>
            <li>Set Dashboard expiry alerts at 90, 60, and 30 days before license end</li>
            <li>Confirm device count in Dashboard matches physical inventory to avoid
                under-licensing surprises at renewal</li>
            <li>Ensure Advanced Security (AMP, IDS/IPS) tier licenses are applied to all
                MX appliances — base licenses do not include threat prevention features</li>
            <li>Consider co-termination or EA consolidation to reduce renewal complexity
                across multiple license key expiry dates</li>
          </ul>
        </div>
      </div>
    </section>
    """

    # =========================================================
    # SECTION 10: CLIENT ANALYSIS
    # =========================================================
    ssid_counts: Dict[str, int] = {}
    os_counts: Dict[str, int] = {}
    vlan_counts: Dict[str, int] = {}
    auth_counts: Dict[str, int] = {}
    rssi_buckets = {"Excellent (>-60)": 0, "Good (-60 to -70)": 0,
                    "Fair (-70 to -80)": 0, "Poor (<-80)": 0}

    for cl in wireless_clients:
        ssid = cl.get("ssid") or "Unknown"
        ssid_counts[ssid] = ssid_counts.get(ssid, 0) + 1

        os_raw = cl.get("os") or cl.get("deviceTypePrediction") or "Unknown"
        os_counts[os_raw] = os_counts.get(os_raw, 0) + 1

        vlan = str(cl.get("vlan") or cl.get("vlanId") or "—")
        vlan_counts[vlan] = vlan_counts.get(vlan, 0) + 1

        auth = cl.get("status") or cl.get("authType") or "Unknown"
        auth_counts[auth] = auth_counts.get(auth, 0) + 1

        rssi = cl.get("rssi")
        if rssi is not None:
            try:
                rssi_val = int(rssi)
                if rssi_val > -60:
                    rssi_buckets["Excellent (>-60)"] += 1
                elif rssi_val >= -70:
                    rssi_buckets["Good (-60 to -70)"] += 1
                elif rssi_val >= -80:
                    rssi_buckets["Fair (-70 to -80)"] += 1
                else:
                    rssi_buckets["Poor (<-80)"] += 1
            except (ValueError, TypeError):
                pass

    def _top_rows(d: Dict[str, int], limit: int = 10) -> str:
        rows = sorted(d.items(), key=lambda x: x[1], reverse=True)[:limit]
        return "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)

    rssi_rows = "".join(
        f"<tr><td>{bucket}</td><td>{cnt}</td></tr>"
        for bucket, cnt in rssi_buckets.items()
    )

    if wireless_clients:
        client_tables = f"""
        <h3>Clients by SSID</h3>
        <table class="data">
          <thead><tr><th>SSID</th><th>Client Count</th></tr></thead>
          <tbody>{_top_rows(ssid_counts)}</tbody>
        </table>

        <h3>Clients by OS / Device Type</h3>
        <table class="data">
          <thead><tr><th>OS / Device Type</th><th>Client Count</th></tr></thead>
          <tbody>{_top_rows(os_counts)}</tbody>
        </table>

        <h3>Clients by VLAN</h3>
        <table class="data">
          <thead><tr><th>VLAN</th><th>Client Count</th></tr></thead>
          <tbody>{_top_rows(vlan_counts)}</tbody>
        </table>

        <h3>Signal Strength Distribution</h3>
        <table class="data">
          <thead><tr><th>RSSI Range</th><th>Client Count</th></tr></thead>
          <tbody>{rssi_rows}</tbody>
        </table>
        """
    else:
        client_tables = """
        <div class="summary-card">
          <div class="summary-body">No wireless client data available in this backup.</div>
        </div>"""

    client_analysis_html = f"""
    <section id="client-analysis" class="report-section">
      <h2>15. Client Analysis</h2>
      <p>Analysis of <strong>{len(wireless_clients)}</strong> wireless client record(s) captured
         in this backup. Wired client detail requires switch port client data which is not
         collected in the current pipeline.</p>
      {client_tables}
    </section>
    """

    # =========================================================
    # SECTION 17: UNIFI COMPARISON & REFRESH PLANNING
    # =========================================================
    # Heuristic model mapping: Meraki family -> UniFi equivalent + indicative USD street price
    # Prices are published MSRP / street estimates (2025–2026) and carry a planning-only disclaimer.
    _UNIFI_MAP = {
        # MX appliances -> UniFi Dream Machine / Cloud Gateway
        "MX68":   ("UDM SE",          1_299,  649),
        "MX75":   ("UCG-Ultra",        599,   299),
        "MX85":   ("UDM Pro Max",    1_999,  899),
        "MX95":   ("UDM Pro Max",    1_999,  899),
        "MX105":  ("UDM Pro SE",     1_499,  699),
        "MX250":  ("UCG-Enterprise", 3_999, 1_799),
        "MX450":  ("UCG-Enterprise", 3_999, 1_799),
        # MS switches -> UniFi USW Pro / Aggregation
        "MS120":  ("USW Lite 16 PoE",  349,   179),
        "MS125":  ("USW Pro 24 PoE",   849,   549),
        "MS210":  ("USW Pro 24",       649,   399),
        "MS220":  ("USW Pro 24",       649,   399),
        "MS225":  ("USW Pro 24 PoE",   849,   549),
        "MS250":  ("USW Pro 48 PoE", 1_299,   799),
        "MS320":  ("USW Pro Aggregation", 999, 699),
        "MS350":  ("USW Enterprise 24 PoE", 1_299, 899),
        "MS390":  ("USW Enterprise 48 PoE", 1_799, 1_199),
        "MS410":  ("USW Aggregation",  799,   499),
        "MS420":  ("USW Aggregation",  799,   499),
        "MS425":  ("USW Pro Aggregation", 999, 699),
        "MS450":  ("USW Pro Aggregation", 999, 699),
        # MR access points -> UniFi U6 / U7 series
        "MR18":   ("U6 Lite",        199,   109),
        "MR20":   ("U6 Lite",        199,   109),
        "MR28":   ("U6 Mesh",        199,   129),
        "MR30":   ("U6 LR",          299,   169),
        "MR33":   ("U6 LR",          299,   169),
        "MR36":   ("U6 Pro",         349,   189),
        "MR42":   ("U6 Pro",         349,   189),
        "MR44":   ("U7 Pro",         499,   299),
        "MR46":   ("U7 Pro",         499,   299),
        "MR46E":  ("U7 Pro Max",     699,   449),
        "MR52":   ("U7 Pro Max",     699,   449),
        "MR55":   ("U7 Pro Max",     699,   449),
        "MR56":   ("U7 Pro Max",     699,   449),
        "MR57":   ("U7 Pro Max",     699,   449),
        "MR70":   ("U6 Mesh",        199,   129),
        "MR74":   ("U6 Mesh Pro",    299,   179),
        "MR76":   ("U7 Outdoor",     499,   299),
        "MR84":   ("U7 Pro Max",     699,   449),
        "MR86":   ("U7 Outdoor",     499,   299),
    }

    _unifi_rows = ""
    _meraki_total = 0
    _unifi_total  = 0
    _eol_swap_meraki = 0
    _eol_swap_unifi  = 0

    for _model, _count in top_models:
        _mprefix = str(_model).upper()
        _map_key = next(
            (k for k in _UNIFI_MAP if _mprefix.startswith(k)),
            None,
        )
        if not _map_key:
            continue
        _unifi_name, _meraki_price, _unifi_price = _UNIFI_MAP[_map_key]
        _is_eol = any(_mprefix.startswith(p) for p in _EOL_PREFIXES)
        _row_mx  = _meraki_price * _count
        _row_ux  = _unifi_price  * _count
        _meraki_total += _row_mx
        _unifi_total  += _row_ux
        if _is_eol:
            _eol_swap_meraki += _row_mx
            _eol_swap_unifi  += _row_ux
        _unifi_rows += (
            f"<tr>"
            f"<td>{_he(_model)}</td>"
            f"<td>{_count}</td>"
            f"<td>{_he(_unifi_name)}</td>"
            f"<td>${_meraki_price:,}</td>"
            f"<td>${_unifi_price:,}</td>"
            f"<td>${_row_mx:,}</td>"
            f"<td>${_row_ux:,}</td>"
            f'<td>{"⚠ EOL" if _is_eol else "—"}</td>'
            f"</tr>"
        )

    _savings = _meraki_total - _unifi_total
    _savings_pct = round(100 * _savings / _meraki_total) if _meraki_total else 0

    if _unifi_rows:
        _unifi_hw_table = f"""
        <table class="data dense">
          <thead>
            <tr>
              <th>Meraki Model</th><th>Qty</th><th>UniFi Equivalent</th>
              <th>Meraki Unit (est.)</th><th>UniFi Unit (est.)</th>
              <th>Meraki Total</th><th>UniFi Total</th><th>Flag</th>
            </tr>
          </thead>
          <tbody>{_unifi_rows}</tbody>
          <tfoot>
            <tr>
              <td colspan="5"><strong>Hardware totals (mapped devices only)</strong></td>
              <td><strong>${_meraki_total:,}</strong></td>
              <td><strong>${_unifi_total:,}</strong></td>
              <td><strong>−{_savings_pct}%</strong></td>
            </tr>
          </tfoot>
        </table>"""
    else:
        _unifi_hw_table = (
            '<div class="summary-card"><div class="summary-body">'
            "No device models in this inventory matched the UniFi comparison table. "
            "Verify model names in inventory_summary.json."
            "</div></div>"
        )

    unifi_html = f"""
    <section id="unifi-comparison" class="report-section">
      <h2>17. UniFi Comparison &amp; Refresh Planning</h2>
      <p>This section provides a heuristic cost comparison between the current Meraki
         environment and a notional UniFi replacement. It is a planning estimate only — not
         a procurement quote or a recommendation to replace. Prices are approximate 2025–2026
         street/MSRP estimates and will vary by reseller, volume, and configuration.
         <strong>Always validate with current partner pricing before presenting externally.</strong></p>

      <div class="summary-card">
        <div class="summary-title">Planning Summary</div>
        <div class="summary-body">
          Mapped devices: {len([r for r in top_models if any(str(r[0]).upper().startswith(k) for k in _UNIFI_MAP)])} model type(s)
          · Meraki hardware estimate: <strong>${_meraki_total:,}</strong>
          · UniFi hardware estimate: <strong>${_unifi_total:,}</strong>
          · Estimated hardware delta: <strong>${_savings:,} ({_savings_pct}% lower)</strong>
          {f"· EOL devices (hardware only): Meraki <strong>${_eol_swap_meraki:,}</strong> vs UniFi <strong>${_eol_swap_unifi:,}</strong>" if _eol_swap_meraki else ""}
          <br><br>
          <em>Note: Meraki hardware prices above do not include annual licensing (typically
          $X–$Y per device per year for Enterprise tier). UniFi has no recurring per-device
          subscription fees beyond optional UniFi OS Cloud (optional, ~$29/mo for remote management).</em>
        </div>
      </div>

      {_unifi_hw_table}

      <h3>Licensing &amp; Support Model Comparison</h3>
      <table class="data">
        <thead>
          <tr><th>Factor</th><th>Cisco Meraki</th><th>Ubiquiti UniFi</th></tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Licensing model</strong></td>
            <td>Mandatory annual per-device license (co-term or Enterprise Agreement).
                Devices enter limited mode without active license.</td>
            <td>No per-device license fees. Hardware purchased once. Optional cloud
                management subscription (~$29/mo).</td>
          </tr>
          <tr>
            <td><strong>Management platform</strong></td>
            <td>Meraki Dashboard (cloud-only SaaS). Full-featured, zero on-prem required.
                Dashboard unavailable without internet or during Meraki outages.</td>
            <td>UniFi Network Application (self-hosted on UDM or Linux server, or UniFi Cloud).
                Can operate fully on-prem without internet dependency.</td>
          </tr>
          <tr>
            <td><strong>Security features</strong></td>
            <td>AMP, IDS/IPS, content filtering, SD-WAN, AutoVPN — Enterprise tier required
                for full threat protection.</td>
            <td>IDS/IPS (Teleport, basic), content filtering (limited), no SD-WAN.
                Security depth is lower on MX-equivalent features.</td>
          </tr>
          <tr>
            <td><strong>Wireless</strong></td>
            <td>Strong enterprise RF management, auto-channel, airtime fairness, band steering.
                Mature roaming (802.11r/k/v) across all current MR models.</td>
            <td>Strong Wi-Fi 6/6E hardware. RF management improving but less mature than Meraki
                in dense multi-AP environments. Roaming support improving in newer firmware.</td>
          </tr>
          <tr>
            <td><strong>Switching</strong></td>
            <td>Full cloud-managed stack with per-port telemetry, STP, ACLs, QoS, LACP.
                Excellent visibility. PoE budget management strong.</td>
            <td>Comparable L2/L3 feature set at lower cost. STP, ACLs, LACP, VLAN, QoS.
                Less real-time telemetry granularity in cloud view.</td>
          </tr>
          <tr>
            <td><strong>Lifecycle / support</strong></td>
            <td>Firmware delivered via Dashboard; end-of-support dates published.
                TAC support included with license. Hardware warranty separate.</td>
            <td>Firmware self-managed (auto-update configurable). Community forums primary
                support channel; hardware warranty typically 1 year.</td>
          </tr>
          <tr>
            <td><strong>Migration complexity</strong></td>
            <td colspan="2">Full replacement requires re-cabling where physical form factor differs,
                VLAN/SSID reconfiguration, staff retraining, and thorough parallel-run testing.
                Budget 20–30% of hardware cost for professional services on a full migration.</td>
          </tr>
        </tbody>
      </table>

      <h3>Recommendation</h3>
      <div class="summary-card">
        <div class="summary-body">
          {"<strong>EOL hardware refresh is the most pressing decision.</strong> The " + str(len(_eol_models)) + " EOL model(s) identified (" + ", ".join(_eol_models[:4]) + ") represent the highest-risk devices. Whether they are refreshed with new Meraki hardware or replaced with an alternative platform, they should exit production within the next 12–18 months." if _eol_models else "<strong>No EOL hardware was flagged.</strong> The refresh decision is less time-pressured."}
          <br><br>
          If the primary driver is licensing cost reduction, a phased migration starting with
          access switching and APs (lowest migration complexity) can deliver savings without
          replacing edge appliances. If operational simplicity and feature parity are priorities,
          retaining Meraki at the edge and evaluating UniFi for access-layer devices is a
          common hybrid approach.
          <br><br>
          Any migration decision should be preceded by a formal RFP or vendor evaluation
          incorporating current pricing, professional services scope, and a pilot deployment.
        </div>
      </div>
    </section>
    """

    full_body = (
        cover_html
        + _schema_banner
        + toc_html
        + exec_html
        + network_overview_html
        + topology_html
        + traffic_html
        + issues_html
        + poe_html
        + security_html
        + recommendations_html
        + cis8_html
        + licensing_html
        + config_coverage_html
        + budget_forecast_html
        + wan_capacity_html
        + ap_interference_html
        + client_analysis_html
        + switch_deep_dive_html
        + unifi_html
    )
    exec_body = cover_html + _schema_banner + exec_html
    backup_body = (
        cover_html
        + _schema_banner
        + toc_backup_html
        + network_overview_html
        + topology_html
        + traffic_html
        + issues_html
        + poe_html
        + security_html
        + recommendations_html
        + cis8_html
        + licensing_html
        + config_coverage_html
        + budget_forecast_html
        + wan_capacity_html
        + ap_interference_html
        + client_analysis_html
        + switch_deep_dive_html
        + unifi_html
    )

    if report_kind == "exec":
        return exec_body
    if report_kind == "backup":
        return backup_body
    return full_body

def main() -> int:
    org_dirs = find_org_dirs(BACKUPS_DIR)
    if not org_dirs:
        log.error("No org directories found in %s/", BACKUPS_DIR)
        log.error("Run meraki_backup.py first.")
        return 1

    generated = 0
    for org_dir in org_dirs:
        # Read display name from org_name.txt; fall back to directory name
        name_file = os.path.join(org_dir, "org_name.txt")
        if os.path.exists(name_file):
            with open(name_file, "r", encoding="utf-8") as nf:
                org_name = nf.read().strip()
        else:
            # Legacy fallback: derive from recommendations.md header
            org_name = os.path.basename(org_dir)
            rec_path = os.path.join(org_dir, "recommendations.md")
            if os.path.exists(rec_path):
                with open(rec_path, "r", encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    m = re.match(r"# Meraki Recommendations: (.+)$", first_line)
                    if m:
                        org_name = m.group(1)

        log.info("Generating report for: %s", org_name)
        _run_ts = datetime.now()
        body = build_org_report(org_dir, org_name)
        html = build_html(f"{org_name} — Network Health Report", body)

        _slug = _report_slug(org_name)
        _stamp = _run_ts.strftime("%Y-%m-%d_%H%M")
        html_path = os.path.join(org_dir, f"{_slug}_{_stamp}_report.html")
        pdf_path  = os.path.join(org_dir, f"{_slug}_{_stamp}_report.pdf")
        named_html_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Complete", _run_ts, "html")
        )
        named_pdf_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Complete", _run_ts, "pdf")
        )
        # Compatibility aliases so older downstream scripts still find report.html/pdf.
        html_alias = os.path.join(org_dir, "report.html")
        pdf_alias  = os.path.join(org_dir, "report.pdf")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        # Overwrite aliases (plain copies — no symlinks for cross-platform safety).
        for alias in (named_html_alias, html_alias):
            with open(alias, "w", encoding="utf-8") as f:
                f.write(html)

        ok = write_pdf(html_path, pdf_path)
        if ok:
            shutil.copy2(pdf_path, named_pdf_alias)
            shutil.copy2(pdf_path, pdf_alias)
            log.info("PDF → %s", named_pdf_alias)
        else:
            log.info("HTML → %s  (no PDF tool found)", html_path)
        generated += 1

        # Executive summary split report
        exec_body = build_org_report(org_dir, org_name, report_kind="exec")
        exec_html = build_html(f"{org_name} — Executive Summary", exec_body)
        exec_html_path = os.path.join(org_dir, f"{_slug}_{_stamp}_exec_summary_report.html")
        exec_pdf_path  = os.path.join(org_dir, f"{_slug}_{_stamp}_exec_summary_report.pdf")
        exec_named_html_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "html")
        )
        exec_named_pdf_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "pdf")
        )
        exec_html_alias = os.path.join(org_dir, "report_exec_summary.html")
        exec_pdf_alias  = os.path.join(org_dir, "report_exec_summary.pdf")
        with open(exec_html_path, "w", encoding="utf-8") as f:
            f.write(exec_html)
        for alias in (exec_named_html_alias, exec_html_alias):
            with open(alias, "w", encoding="utf-8") as f:
                f.write(exec_html)
        if write_pdf(exec_html_path, exec_pdf_path):
            shutil.copy2(exec_pdf_path, exec_named_pdf_alias)
            shutil.copy2(exec_pdf_path, exec_pdf_alias)
            log.info("Exec Summary PDF → %s", exec_named_pdf_alias)
        else:
            log.info("Exec Summary HTML → %s  (no PDF tool found)", exec_html_path)

        # Backup settings split report
        backup_body = build_org_report(org_dir, org_name, report_kind="backup")
        backup_html = build_html(f"{org_name} — Backup Settings Report", backup_body)
        backup_html_path = os.path.join(org_dir, f"{_slug}_{_stamp}_backup_settings_report.html")
        backup_pdf_path  = os.path.join(org_dir, f"{_slug}_{_stamp}_backup_settings_report.pdf")
        backup_named_html_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "html")
        )
        backup_named_pdf_alias = os.path.join(
            org_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "pdf")
        )
        backup_html_alias = os.path.join(org_dir, "report_backup_settings.html")
        backup_pdf_alias  = os.path.join(org_dir, "report_backup_settings.pdf")
        with open(backup_html_path, "w", encoding="utf-8") as f:
            f.write(backup_html)
        for alias in (backup_named_html_alias, backup_html_alias):
            with open(alias, "w", encoding="utf-8") as f:
                f.write(backup_html)
        if write_pdf(backup_html_path, backup_pdf_path):
            shutil.copy2(backup_pdf_path, backup_named_pdf_alias)
            shutil.copy2(backup_pdf_path, backup_pdf_alias)
            log.info("Backup Settings PDF → %s", backup_named_pdf_alias)
        else:
            log.info("Backup Settings HTML → %s  (no PDF tool found)", backup_html_path)

    log.info("Done — %d report(s) generated.", generated)
    return 0
