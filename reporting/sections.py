import math
import os

from typing import Any, Dict, List, Optional, Tuple

from .common import (
    _describe_port_neighbor,
    _describe_vlan_mode,
    _format_usage_kb,
    _he,
    _inline_md,
    _is_sfp_like_port,
    _model_capability_summary,
    _hardware_consistency_note,
    _port_group_label,
    _port_heat_label,
    _port_heat_score,
    _port_role_label,
    _port_role_short,
    _port_sort_key,
    _speed_label,
    _switch_anchor,
    _build_switch_link_narrative,
    render_section,
)
from .topology import _build_topology_facts


def _is_low_speed_link(speed: Any) -> bool:
    text = str(speed or "").strip().lower()
    return text.startswith("10 mb") or text.startswith("100 mb")


def _meaningful_port_messages(messages: Any) -> List[str]:
    if isinstance(messages, str):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    benign_fragments = (
        "disconnected",
        "not connected",
        "no link",
        "link down",
        "down",
    )
    result = []
    for message in messages:
        text = str(message or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if any(fragment in lowered for fragment in benign_fragments):
            continue
        result.append(text)
    return result


def _model_cell(model: Any) -> str:
    text = str(model or "").strip()
    return f"<code>{_he(text)}</code>" if text else "Unknown model"


def _compact_text(value: Any, max_len: int = 18) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)].rstrip() + "…"


def _compact_vlan_text(value: Any) -> str:
    text = str(value or "—").strip()
    replacements = {
        "Trunk": "T",
        "Access": "A",
        "native": "n",
        "allowed": "allow",
        "VLAN": "V",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return _compact_text(text, 24)


def _compact_neighbor_text(port: Dict[str, Any], serial_to_dev: Dict[str, Dict[str, Any]]) -> str:
    text = _describe_port_neighbor(port, serial_to_dev)
    text = text.replace("downstream client(s)", "clients")
    text = text.replace("No neighbor data", "—")
    return _compact_text(text, 26) or "—"


def _render_switch_port_grid(
    ports: List[Dict[str, Any]],
    port_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    serial_to_dev: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    if not ports:
        return '<div class="switch-detail-grid-empty">No port telemetry available.</div>'
    grouped: Dict[str, List[str]] = {}
    counts = {"uplink": 0, "downlink": 0, "endpoint": 0, "unused": 0}
    port_configs = port_configs or {}
    serial_to_dev = serial_to_dev or {}
    for port in sorted(ports, key=lambda item: _port_sort_key(item.get("portId"))):
        port_id = str(port.get("portId") or "?")
        status = str(port.get("status") or "").lower()
        speed = str(port.get("speed") or "")
        errors = _meaningful_port_messages(port.get("errors") or [])
        role = _port_role_label(port, port_configs.get(port_id), serial_to_dev)
        if errors:
            cls = "issue"
        elif port.get("isUplink"):
            cls = "uplink"
        elif "disconnected" in status or "not connected" in status or not status:
            cls = "down"
        elif _is_low_speed_link(speed):
            cls = "warn"
        elif (port.get("poe") or {}).get("isAllocated"):
            cls = "poe"
        else:
            cls = "ok"
        if cls == "uplink":
            counts["uplink"] += 1
        elif role == "Downlink":
            counts["downlink"] += 1
        elif cls == "down":
            counts["unused"] += 1
        else:
            counts["endpoint"] += 1
        speed_label = _speed_label(speed)
        speed_cls = ""
        if speed_label in ("2.5G", "5G"):
            speed_cls = "speed-mgig"
        elif speed_label in ("10G", "25G"):
            speed_cls = "speed-uplink"
        sfp_cls = " sfp-port" if _is_sfp_like_port(port_id) else ""
        grouped.setdefault(_port_group_label(port_id), []).append(
            f'<div class="switch-port-cell {cls}{sfp_cls} {speed_cls}" title="{_he(port_id)} - {_he(role)} - {_he(speed or "Unknown")}">'
            f'<span class="switch-port-num">{_he(port_id)}</span>'
            f'<span class="switch-port-meta">{_he(_port_role_short(role))} {_he(speed_label) if status and cls != "down" else ""}</span>'
            f"</div>"
        )
    summary = (
        '<div class="switch-port-summary">'
        f'<span><strong>{counts["uplink"]}</strong> uplink</span>'
        f'<span><strong>{counts["downlink"]}</strong> downlink</span>'
        f'<span><strong>{counts["endpoint"]}</strong> edge</span>'
        f'<span><strong>{counts["unused"]}</strong> down</span>'
        '</div>'
    )
    groups_html = []
    for label, cells in grouped.items():
        midpoint = max(1, math.ceil(len(cells) / 2))
        group_kind = "SFP / Module" if label != "Front Panel" else "Front Panel"
        row_one = "".join(cells[:midpoint])
        row_two = "".join(cells[midpoint:])
        groups_html.append(
            f'<div class="switch-port-group">'
            f'<div class="switch-port-group-title">{_he(label)} <span class="switch-port-group-kind">{_he(group_kind)}</span></div>'
            f'<div class="switch-port-face">'
            f'<div class="switch-port-row">{row_one}</div>'
            f'{f"<div class=\"switch-port-row\">{row_two}</div>" if row_two else ""}'
            f"</div></div>"
        )
    return summary + "".join(groups_html)


def _build_switch_detail_section(
    devices_by_network: Dict[str, Dict[str, Any]],
    lldp_cdp: Dict[str, Any],
    switch_port_statuses_by_switch: Dict[str, Any],
    switch_port_configs_by_switch: Dict[str, Any],
    poe_by_serial: Dict[str, Dict[str, Any]],
    port_issues_by_switch: Dict[str, List[Dict[str, Any]]],
    hardware_catalog: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Tuple[str, str]]]:
    catalog_models = (hardware_catalog or {}).get("models") or {}
    switch_entries: List[Tuple[str, str, str, str]] = []
    for net_data in sorted(devices_by_network.values(), key=lambda item: item["name"]):
        for dev in sorted(
            [d for d in net_data["devices"] if d.get("productType") == "switch"],
            key=lambda item: item.get("name") or item.get("serial") or "",
        ):
            switch_entries.append(
                (
                    net_data["name"],
                    dev.get("serial") or "",
                    dev.get("name") or dev.get("model") or dev.get("serial") or "Switch",
                    dev.get("model") or "",
                )
            )

    toc_items = [(_switch_anchor(serial, name), f"{site} - {name}") for site, serial, name, _ in switch_entries]
    if not switch_entries:
        return (
            """
    <section id="switch-deep-dive" class="report-section">
      <h2>16. Switch Deep Dive</h2>
      <div class="summary-card"><div class="summary-body">No switch inventory was available for detailed port-level analysis.</div></div>
    </section>
    """,
            toc_items,
        )

    all_devices = [
        device
        for net_data in devices_by_network.values()
        for device in net_data.get("devices", [])
        if isinstance(device, dict)
    ]
    serial_to_dev, status_by_switch, parent_of, children_of, edge_counts = _build_topology_facts(
        all_devices, lldp_cdp, switch_port_statuses_by_switch
    )
    switches_with_port_status = sum(
        1 for _, serial, _, _ in switch_entries
        if status_by_switch.get(serial)
    )
    switches_with_lldp = sum(
        1 for _, serial, _, _ in switch_entries
        if isinstance(lldp_cdp, dict) and lldp_cdp.get(serial)
    )
    identity_rows = []
    for site_name, serial, switch_name, model in switch_entries:
        switch = serial_to_dev.get(serial, {})
        ports = status_by_switch.get(serial, {})
        port_count = len(ports)
        connected_ports = sum(
            1 for port in ports.values()
            if str(port.get("status") or "").lower() == "connected"
        )
        poe_data = poe_by_serial.get(serial, {})
        observed_watts = float(poe_data.get("avgWatts", 0) or 0)
        reference = catalog_models.get(model) or {}
        budget = reference.get("poeBudgetWatts")
        headroom = "Unknown"
        if isinstance(budget, (int, float)):
            headroom = f"{max(0.0, float(budget) - observed_watts):.1f} W"
        identity_rows.append(
            "<tr>"
            f"<td>{_he(site_name)}</td>"
            f"<td>{_he(switch_name)}<br><code>{_he(serial)}</code></td>"
            f"<td>{_he(model or '—')}</td>"
            f"<td>{_he(str(switch.get('status') or 'unknown'))}</td>"
            f"<td>{connected_ports} / {port_count if port_count else '—'}</td>"
            f"<td>{_he(f'{budget} W' if isinstance(budget, (int, float)) else 'Unknown')}</td>"
            f"<td>{observed_watts:.1f} W</td>"
            f"<td>{_he(headroom)}</td>"
            f"<td>{_he(str(reference.get('source') or 'Not in local catalog'))}</td>"
            "</tr>"
        )

    section_parts = [
        f"""
    <section id="switch-deep-dive" class="report-section">
      <h2>16. Switch Deep Dive</h2>
      <p>Port-level views for each MS switch, including link status, negotiated speed, traffic, PoE draw, inferred connected device, and upstream/downstream placement in the switching tree.</p>
      <div class="summary-card">
        <div class="summary-title">Source Data Coverage</div>
        <div class="summary-body">
          Switches discovered: <strong>{len(switch_entries)}</strong> ·
          Port telemetry available: <strong>{switches_with_port_status}</strong> ·
          LLDP/CDP neighbor data available: <strong>{switches_with_lldp}</strong>.
          If this section appears sparse, regenerate backups with a full API collection and confirm the
          Dashboard API key can read switch port statuses, switch port configs, and LLDP/CDP data.
        </div>
      </div>
      <h3>Switch Identity &amp; PoE Budget Reference</h3>
      <table class="data dense switch-identity-table">
        <thead>
          <tr><th>Site</th><th>Switch</th><th>Model</th><th>Status</th><th>Ports Up</th><th>Known PoE Budget</th><th>Observed PoE Avg</th><th>Budget Headroom</th><th>Reference</th></tr>
        </thead>
        <tbody>{''.join(identity_rows)}</tbody>
      </table>
    </section>
    """
    ]

    for site_name, serial, switch_name, model in switch_entries:
        switch = serial_to_dev.get(serial, {})
        ports = sorted(
            status_by_switch.get(serial, {}).values(),
            key=lambda item: (
                0,
                int(str(item.get("portId", "0"))) if str(item.get("portId", "")).isdigit() else 0,
                str(item.get("portId") or ""),
            ),
        )
        port_configs = {
            str(p.get("portId")): p
            for p in (switch_port_configs_by_switch.get(serial) or [])
            if isinstance(p, dict)
        }
        parent = parent_of.get(serial)
        parent_name = (
            serial_to_dev.get(parent[0], {}).get("name") or parent[0]
            if parent else "Internet edge"
        )
        child_names = [
            serial_to_dev.get(child, {}).get("name") or child
            for child in children_of.get(serial, [])
        ]
        issue_count = len(port_issues_by_switch.get(serial, []))
        poe_data = poe_by_serial.get(serial, {})
        poe_watts = float(poe_data.get("avgWatts", 0) or 0)
        hardware_reference = catalog_models.get(model) or {}
        poe_budget = hardware_reference.get("poeBudgetWatts")
        poe_budget_text = f"{poe_budget} W" if isinstance(poe_budget, (int, float)) else "Unknown"
        poe_headroom_text = "Unknown"
        if isinstance(poe_budget, (int, float)):
            poe_headroom_text = f"{max(0.0, float(poe_budget) - poe_watts):.1f} W"
        active_ports = sum(1 for port in ports if str(port.get("status") or "").lower() == "connected")
        uplink_ports = [port for port in ports if port.get("isUplink")]
        ranked_ports = sorted(
            ports,
            key=lambda port: (-_port_heat_score(port), _port_sort_key(port.get("portId"))),
        )
        hottest_ports = [
            f"{port.get('portId')} ({_port_heat_label(_port_heat_score(port)).lower()} {_port_heat_score(port):.0f})"
            for port in ranked_ports[:5]
            if _port_heat_score(port) >= 15
        ]
        link_narrative = _build_switch_link_narrative(
            serial,
            parent,
            child_names,
            uplink_ports,
            edge_counts.get(serial, 0),
            serial_to_dev,
        )
        table_rows = []
        for port in ranked_ports:
            port_id = str(port.get("portId") or "")
            port_config = port_configs.get(port_id)
            usage = port.get("usageInKb") or {}
            traffic = port.get("trafficInKbps") or {}
            errors = _meaningful_port_messages(port.get("errors") or [])
            warnings = _meaningful_port_messages(port.get("warnings") or [])
            poe = port.get("poe") or {}
            power_wh = port.get("powerUsageInWh")
            indicators = []
            if port.get("isUplink"):
                indicators.append('<span class="badge badge-info">U</span>')
            if poe.get("isAllocated") or (isinstance(power_wh, (int, float)) and power_wh > 0):
                indicators.append('<span class="badge badge-ok">P</span>')
            if errors:
                indicators.append(f'<span class="badge badge-fail">E{len(errors)}</span>')
            elif warnings:
                indicators.append(f'<span class="badge badge-warn">W{len(warnings)}</span>')
            speed = str(port.get("speed") or "—")
            if _is_low_speed_link(speed):
                indicators.append(f'<span class="badge badge-warn">{_he(_speed_label(speed))}</span>')
            role = _port_role_label(port, port_config, serial_to_dev)
            vlan_text = _describe_vlan_mode(port_config)
            port_name = "—"
            if isinstance(port_config, dict):
                port_name = str(port_config.get("name") or "—")
            heat_score = _port_heat_score(port)
            heat_label = _port_heat_label(heat_score)
            heat_badge_cls = {
                "Hot": "badge-fail",
                "Warm": "badge-warn",
                "Cool": "badge-info",
                "Idle": "badge-ok",
            }[heat_label]
            table_rows.append(
                "<tr>"
                f"<td>{_he(port_id or '—')}</td>"
                f"<td title=\"{_he(port_name)}\">{_he(_compact_text(port_name, 16) or '—')}</td>"
                f"<td><span class=\"badge {heat_badge_cls}\">{_he(heat_label[:1])}{heat_score:.0f}</span></td>"
                f"<td>{_he(_port_role_short(role))}</td>"
                f"<td>{_he(_compact_text(str(port.get('status') or 'Unknown'), 9))}</td>"
                f"<td>{_he(_speed_label(speed))}</td>"
                f"<td>{_he(_compact_text(str(port.get('duplex') or '—'), 4))}</td>"
                f"<td title=\"{_he(vlan_text)}\">{_he(_compact_vlan_text(vlan_text))}</td>"
                f"<td>{_format_usage_kb((usage or {}).get('total'))}</td>"
                f"<td>{_he(str((traffic or {}).get('total') or '—'))} Kbps</td>"
                f"<td>{_he(f'{float(power_wh):.1f} Wh' if isinstance(power_wh, (int, float)) else ('Allocated' if poe.get('isAllocated') else '—'))}</td>"
                f"<td>{''.join(indicators) or '—'}</td>"
                f"<td title=\"{_he(_describe_port_neighbor(port, serial_to_dev))}\">{_inline_md(_compact_neighbor_text(port, serial_to_dev))}</td>"
                "</tr>"
            )

        section_parts.append(
            f"""
    <section id="{_switch_anchor(serial, switch_name)}" class="report-section switch-detail-page">
      <h3>{_he(switch_name)}</h3>
      <p class="switch-detail-kicker">{_he(site_name)} &mdash; {_he(model or 'MS switch')} &mdash; <code>{_he(serial)}</code></p>
      <div class="switch-detail-stats">
        <div class="switch-detail-stat"><span class="label">Above</span><span class="value">{_he(parent_name if not parent else f'{parent_name} ({parent[1]} -> {parent[2] or "?"})')}</span></div>
        <div class="switch-detail-stat"><span class="label">Below</span><span class="value">{_he(', '.join(child_names) if child_names else 'No downstream switches discovered')}</span></div>
        <div class="switch-detail-stat"><span class="label">Edge Devices</span><span class="value">{edge_counts.get(serial, 0)}</span></div>
        <div class="switch-detail-stat"><span class="label">Ports Up</span><span class="value">{active_ports} / {len(ports) or 0}</span></div>
        <div class="switch-detail-stat"><span class="label">Uplinks</span><span class="value">{_he(', '.join(str(port.get('portId')) for port in uplink_ports) if uplink_ports else 'None flagged')}</span></div>
        <div class="switch-detail-stat"><span class="label">PoE Avg</span><span class="value">{poe_watts:.1f} W</span></div>
        <div class="switch-detail-stat"><span class="label">PoE Budget</span><span class="value">{_he(poe_budget_text)}</span></div>
        <div class="switch-detail-stat"><span class="label">PoE Headroom</span><span class="value">{_he(poe_headroom_text)}</span></div>
        <div class="switch-detail-stat"><span class="label">Port Issues</span><span class="value">{issue_count}</span></div>
      </div>
        <div class="switch-detail-card">
        <div class="summary-body switch-detail-narrative">{_he(link_narrative)}</div>
        <div class="summary-body switch-detail-narrative"><strong>Heat ranking:</strong> {_he(', '.join(hottest_ports) if hottest_ports else 'No materially busy ports detected in current telemetry.')}</div>
        <div class="summary-title">Port Map</div>
        {_render_switch_port_grid(ports, port_configs, serial_to_dev)}
        <div class="switch-detail-legend">
          <span><i class="swatch ok"></i>healthy</span>
          <span><i class="swatch uplink"></i>uplink</span>
          <span><i class="swatch poe"></i>PoE</span>
          <span><i class="swatch warn"></i>low speed / warning</span>
          <span><i class="swatch issue"></i>error</span>
          <span><i class="swatch down"></i>down</span>
          <span><i class="swatch speed-mgig"></i>2.5G / multi-gig</span>
          <span><i class="swatch speed-uplink"></i>10G+ / high-speed uplink</span>
          <span><i class="swatch sfp"></i>SFP / module port</span>
        </div>
      </div>
      <table class="data switch-detail-table">
        <colgroup>
          <col class="c-port"><col class="c-label"><col class="c-heat"><col class="c-role">
          <col class="c-status"><col class="c-speed"><col class="c-duplex"><col class="c-vlan">
          <col class="c-total"><col class="c-rate"><col class="c-power"><col class="c-flags"><col class="c-neighbor">
        </colgroup>
        <thead>
          <tr>
            <th>Port</th><th>Label</th><th>Heat</th><th>Role</th><th>Stat</th><th>Spd</th><th>Dup</th><th>VLAN</th>
            <th>Data</th><th>Kbps</th><th>Pwr</th><th>Flg</th><th>Neighbor</th>
          </tr>
        </thead>
        <tbody>{''.join(table_rows) if table_rows else '<tr><td colspan=\"13\">No switch port status data available.</td></tr>'}</tbody>
      </table>
    </section>
    """
        )

    return "".join(section_parts), toc_items


def _build_ap_interference_section(
    devices_by_network: Dict[str, Dict[str, Any]],
    channel_util: Any,
    wireless_stats: Dict[str, Any],
    switch_port_statuses_by_switch: Dict[str, Any],
) -> str:
    if not isinstance(channel_util, list):
        return """
    <section id="ap-interference" class="report-section">
      <h2>14. AP Interference Audit</h2>
      <div class="summary-card"><div class="summary-body">No AP channel utilization data was available for interference analysis.</div></div>
    </section>
    """

    networks_by_serial: Dict[str, str] = {}
    ap_by_serial: Dict[str, Dict[str, Any]] = {}
    for net_id, net_data in devices_by_network.items():
        for dev in net_data.get("devices", []):
            if dev.get("productType") == "wireless" and dev.get("serial"):
                networks_by_serial[dev["serial"]] = net_id
                ap_by_serial[dev["serial"]] = dev

    per_ap_rows: List[Dict[str, Any]] = []
    site_summary: Dict[str, Dict[str, Any]] = {}
    for row in channel_util:
        if not isinstance(row, dict):
            continue
        serial = row.get("serial")
        net_id = (row.get("network") or {}).get("id") or networks_by_serial.get(serial) or "unassigned"
        net_data = devices_by_network.get(net_id, {"name": "Unassigned"})
        ap = ap_by_serial.get(serial, {})
        stats = []
        for band in row.get("byBand") or []:
            if not isinstance(band, dict):
                continue
            wifi = float(((band.get("wifi") or {}).get("percentage")) or 0)
            non_wifi = float(((band.get("nonWifi") or {}).get("percentage")) or 0)
            total = float(((band.get("total") or {}).get("percentage")) or 0)
            stats.append(
                {
                    "band": str(band.get("band") or "?"),
                    "wifi": wifi,
                    "non_wifi": non_wifi,
                    "total": total,
                }
            )
        if not stats:
            continue
        worst = max(stats, key=lambda item: (item["non_wifi"], item["total"], item["wifi"]))
        avg_total = sum(item["total"] for item in stats) / len(stats)
        avg_non_wifi = sum(item["non_wifi"] for item in stats) / len(stats)
        avg_wifi = sum(item["wifi"] for item in stats) / len(stats)
        conn = None
        for item in wireless_stats.get(net_id, []) if isinstance(wireless_stats, dict) else []:
            if isinstance(item, dict) and item.get("serial") == serial:
                conn = item.get("connectionStats") or {}
                break
        assoc = int((conn or {}).get("assoc") or 0)
        auth = int((conn or {}).get("auth") or 0)
        success = int((conn or {}).get("success") or 0)
        if worst["non_wifi"] >= 25 or worst["total"] >= 75:
            severity = "High"
            severity_cls = "check-fail"
        elif worst["non_wifi"] >= 10 or worst["total"] >= 45:
            severity = "Medium"
            severity_cls = "check-warning"
        else:
            severity = "Low"
            severity_cls = "check-pass"
        findings = []
        if worst["non_wifi"] >= 25:
            findings.append("high non-802.11 interference")
        elif worst["non_wifi"] >= 10:
            findings.append("moderate non-802.11 interference")
        if worst["wifi"] >= 40:
            findings.append("heavy co-channel contention")
        if worst["band"] == "2.4" and worst["total"] >= 40:
            findings.append("crowded 2.4 GHz airtime")
        if success and assoc and (success / max(auth, 1)) < 2:
            findings.append("possible client retry / onboarding friction")
        if not findings:
            findings.append("no major RF symptoms in sampled telemetry")

        recs = []
        if worst["non_wifi"] >= 25:
            recs.append("inspect non-Wi-Fi noise sources near the AP")
        if worst["wifi"] >= 40:
            recs.append("review channel plan and AP density for overlap")
        if worst["band"] == "2.4" and worst["total"] >= 40:
            recs.append("reduce 2.4 GHz reliance and prefer 5 GHz/6 GHz capable clients")
        if assoc > 80:
            recs.append("review load distribution and client balancing")
        if not recs:
            recs.append("continue monitoring; no immediate RF action indicated")

        ap_row = {
            "site": net_data["name"],
            "name": ap.get("name") or serial,
            "serial": serial,
            "model": ap.get("model") or "",
            "status": ap.get("status") or "unknown",
            "band": worst["band"],
            "worst_total": worst["total"],
            "worst_non_wifi": worst["non_wifi"],
            "worst_wifi": worst["wifi"],
            "avg_total": avg_total,
            "avg_non_wifi": avg_non_wifi,
            "assoc": assoc,
            "auth": auth,
            "success": success,
            "severity": severity,
            "severity_cls": severity_cls,
            "findings": findings,
            "recommendations": recs,
        }
        per_ap_rows.append(ap_row)

        site = site_summary.setdefault(
            net_id,
            {"name": net_data["name"], "aps": 0, "high": 0, "avg_non_wifi": 0.0, "avg_total": 0.0, "bands": {}},
        )
        site["aps"] += 1
        site["avg_non_wifi"] += avg_non_wifi
        site["avg_total"] += avg_total
        if severity == "High":
            site["high"] += 1
        band_key = f'{worst["band"]} GHz'
        site["bands"][band_key] = site["bands"].get(band_key, 0) + 1

    if not per_ap_rows:
        return """
    <section id="ap-interference" class="report-section">
      <h2>14. AP Interference Audit</h2>
      <div class="summary-card"><div class="summary-body">APs were present, but no usable per-band channel utilization telemetry was available.</div></div>
    </section>
    """

    site_cards = []
    for site in sorted(site_summary.values(), key=lambda item: item["name"]):
        aps = max(site["aps"], 1)
        site_cards.append(
            f"""
      <div class="summary-card">
        <div class="summary-title">{_he(site['name'])}</div>
        <div class="summary-body">
          <strong>{site['aps']}</strong> APs with RF telemetry,
          <strong>{site['high']}</strong> high-interference APs,
          avg non-Wi-Fi interference <strong>{site['avg_non_wifi']/aps:.1f}%</strong>,
          avg total channel utilization <strong>{site['avg_total']/aps:.1f}%</strong>,
          dominant affected band <strong>{_he(max(site['bands'].items(), key=lambda item: item[1])[0])}</strong>.
        </div>
      </div>
        """
        )

    hot_aps = sorted(
        per_ap_rows,
        key=lambda item: (-item["worst_non_wifi"], -item["worst_total"], item["site"], item["name"]),
    )
    switch_ap_links: Dict[str, List[Dict[str, Any]]] = {}
    for switch_serial, ports in switch_port_statuses_by_switch.items() if isinstance(switch_port_statuses_by_switch, dict) else []:
        if not isinstance(ports, list):
            continue
        for port in ports:
            if not isinstance(port, dict):
                continue
            for key in ("lldp", "cdp"):
                disc = port.get(key)
                if not isinstance(disc, dict):
                    continue
                neighbor_id = str(disc.get("chassisId") or disc.get("deviceId") or "").lower()
                for ap in hot_aps:
                    ap_mac = str(ap_by_serial.get(ap["serial"], {}).get("mac") or "").lower().replace(":", "")
                    if ap_mac and ap_mac in neighbor_id.replace(":", ""):
                        switch_ap_links.setdefault(switch_serial, []).append(
                            {
                                "switch_port": str(port.get("portId") or "?"),
                                "ap": ap,
                            }
                        )
                        break
    ap_deep_dive_parts = []
    for switch_serial, linked in sorted(switch_ap_links.items(), key=lambda item: len(item[1]), reverse=True):
        linked_sorted = sorted(
            linked,
            key=lambda item: (
                {"High": 0, "Medium": 1, "Low": 2}.get(item["ap"]["severity"], 3),
                -item["ap"]["worst_non_wifi"],
                item["switch_port"],
            ),
        )
        rows = "".join(
            "<tr>"
            f"<td>{_he(item['switch_port'])}</td>"
            f"<td>{_he(item['ap']['name'])}</td>"
            f"<td><span class=\"{item['ap']['severity_cls']}\">{_he(item['ap']['severity'])}</span></td>"
            f"<td>{_he(item['ap']['band'])} GHz</td>"
            f"<td>{item['ap']['worst_non_wifi']:.1f}%</td>"
            f"<td>{item['ap']['worst_total']:.1f}%</td>"
            f"<td>{item['ap']['assoc']}</td>"
            f"<td>{_he('; '.join(item['ap']['findings']))}</td>"
            "</tr>"
            for item in linked_sorted[:20]
        )
        ap_deep_dive_parts.append(
            f"""
      <div class="building-section">
        <h4>{_he(switch_serial)}</h4>
        <table class="data dense">
          <thead>
            <tr><th>Port</th><th>AP</th><th>Severity</th><th>Band</th><th>Non-WiFi</th><th>Total</th><th>Assoc</th><th>AP Findings</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
            """
        )
    ap_findings_rows = "".join(
        "<tr>"
        f"<td>{_he(item['site'])}</td>"
        f"<td>{_he(item['name'])}<br><code>{_he(item['serial'])}</code></td>"
        f"<td><span class=\"{item['severity_cls']}\">{_he(item['severity'])}</span></td>"
        f"<td>{_he(item['band'])} GHz</td>"
        f"<td>{item['worst_non_wifi']:.1f}%</td>"
        f"<td>{item['worst_wifi']:.1f}%</td>"
        f"<td>{item['worst_total']:.1f}%</td>"
        f"<td>{item['assoc']}</td>"
        f"<td>{_he('; '.join(item['findings']))}</td>"
        f"<td>{_he('; '.join(item['recommendations']))}</td>"
        "</tr>"
        for item in hot_aps[:25]
    )
    diagnostic_rows = "".join(
        "<tr>"
        f"<td>{_he(item['site'])}</td>"
        f"<td>{_he(item['name'])}</td>"
        f"<td>{_he(item['serial'])}</td>"
        f"<td>{item['avg_non_wifi']:.1f}%</td>"
        f"<td>{item['avg_total']:.1f}%</td>"
        f"<td>{item['assoc']}</td>"
        f"<td>{item['auth']}</td>"
        f"<td>{item['success']}</td>"
        "</tr>"
        for item in hot_aps[:50]
    )

    recommendations = []
    if any(item["worst_non_wifi"] >= 25 for item in hot_aps):
        recommendations.append("Physically inspect high-noise AP locations for microwaves, Bluetooth density, wireless presentation gear, and other non-802.11 emitters.")
    if any(item["worst_wifi"] >= 40 for item in hot_aps):
        recommendations.append("Review channel reuse and AP placement in high-contention areas to reduce co-channel overlap.")
    if any(item["band"] == "2.4" and item["worst_total"] >= 40 for item in hot_aps):
        recommendations.append("Reduce 2.4 GHz dependency where possible by tuning SSIDs, minimum bitrates, and client steering.")
    if not recommendations:
        recommendations.append("No widespread interference hotspot was detected in the sampled dataset; continue trend monitoring.")

    return f"""
    <section id="ap-interference" class="report-section">
      <h2>14. AP Interference Audit</h2>
      <p>This section converts Meraki channel-utilization telemetry into an RF interference view by site and by AP. `non-Wi-Fi` represents likely external RF noise, while `Wi-Fi` represents airtime consumed by neighboring WLAN activity and co-channel contention. Where exact AP neighbor telemetry is unavailable, neighbor pressure is inferred from high Wi-Fi airtime on the affected band.</p>
      {''.join(site_cards)}
      <h3>Priority AP Findings</h3>
      <table class="data dense">
        <thead>
          <tr>
            <th>Site</th><th>AP</th><th>Severity</th><th>Band</th><th>Non-WiFi</th>
            <th>WiFi</th><th>Total</th><th>Assoc</th><th>Findings</th><th>Recommendations</th>
          </tr>
        </thead>
        <tbody>{ap_findings_rows}</tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">RF Recommendations</div>
        <div class="summary-body"><ul>{''.join(f'<li>{_he(item)}</li>' for item in recommendations)}</ul></div>
      </div>
      <h3>AP Deep Dive By Switch</h3>
      {''.join(ap_deep_dive_parts) if ap_deep_dive_parts else '<div class="summary-card"><div class="summary-body">AP-to-switch mapping was not available in the current telemetry, so AP deep dives could not yet be grouped by switch.</div></div>'}
      <h3>Diagnostic Dump</h3>
      <table class="data dense">
        <thead>
          <tr><th>Site</th><th>AP</th><th>Serial</th><th>Avg Non-WiFi</th><th>Avg Total</th><th>Assoc</th><th>Auth</th><th>Success</th></tr>
        </thead>
        <tbody>{diagnostic_rows}</tbody>
      </table>
    </section>
    """


def _build_ap_spectrum_report(
    devices_by_network: Dict[str, Dict[str, Any]],
    channel_util: Any,
    wireless_stats: Dict[str, Any],
    rf_profiles: Any,
    rf_profile_assignments: Any = None,
) -> str:
    def _band_stats(row: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        bands: Dict[str, Dict[str, float]] = {}
        for band in row.get("byBand") or []:
            if not isinstance(band, dict):
                continue
            band_key = str(band.get("band") or "?")
            bands[band_key] = {
                "wifi": float(((band.get("wifi") or {}).get("percentage")) or 0),
                "non_wifi": float(((band.get("nonWifi") or {}).get("percentage")) or 0),
                "total": float(((band.get("total") or {}).get("percentage")) or 0),
            }
        return bands

    def _bubble(stats: Dict[str, float] | None) -> Tuple[str, str]:
        if not stats:
            return ("No telemetry", "check-warning")
        wifi = stats.get("wifi", 0.0)
        total = stats.get("total", 0.0)
        non_wifi = stats.get("non_wifi", 0.0)
        if non_wifi >= 50 and total >= 75:
            return ("External RF saturation / investigate noise", "check-fail")
        if non_wifi >= 25:
            return ("High non-Wi-Fi noise / inspect source", "check-fail")
        if wifi >= 55:
            return ("WAY TOO CLOSE / saturated RF bubble", "check-fail")
        if wifi >= 40:
            return ("Too close / co-channel pressure", "check-fail")
        if non_wifi >= 15:
            return ("Non-Wi-Fi noise / inspect source", "check-warning")
        if total >= 60:
            return ("Saturated airtime / mixed interference", "check-fail")
        if wifi >= 25 or total >= 45:
            return ("Tight bubble / tune placement", "check-warning")
        if wifi >= 10 or total >= 25:
            return ("Within range / acceptable overlap", "check-pass")
        return ("Clean bubble / no overlap symptom", "check-pass")

    def _flatten_assignments(raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            return [item for item in raw["items"] if isinstance(item, dict)]
        if not isinstance(raw, list):
            return []
        rows: List[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("items"), list):
                rows.extend(child for child in item["items"] if isinstance(child, dict))
            elif isinstance(item, dict):
                rows.append(item)
        return rows

    assignment_by_serial = {
        str(item.get("serial")): item
        for item in _flatten_assignments(rf_profile_assignments)
        if item.get("serial")
    }

    def _profile_settings_by_id(net_id: str) -> Dict[str, Dict[str, Any]]:
        profiles = rf_profiles.get(net_id) if isinstance(rf_profiles, dict) else None
        if not isinstance(profiles, list):
            return {}
        return {
            str(profile.get("id")): profile
            for profile in profiles
            if isinstance(profile, dict) and profile.get("id")
        }

    def _format_profile_power(profile: Dict[str, Any], band: str, exact: bool) -> str:
        band_map = {
            "2.4": "twoFourGhzSettings",
            "5": "fiveGhzSettings",
            "6": "sixGhzSettings",
        }
        field = band_map.get(str(band))
        settings = profile.get(field) if field else None
        if not isinstance(settings, dict):
            return "RF profile power not available"
        min_power = settings.get("minPower")
        max_power = settings.get("maxPower")
        min_text = f"{float(min_power):.0f} dBm min" if isinstance(min_power, (int, float)) else "min n/a"
        max_text = f"{float(max_power):.0f} dBm max" if isinstance(max_power, (int, float)) else "max n/a"
        cap_note = ""
        if isinstance(max_power, (int, float)) and max_power <= 17:
            cap_note = "; low power ceiling"
        elif isinstance(max_power, (int, float)) and max_power <= 22:
            cap_note = "; moderate power ceiling"
        elif isinstance(max_power, (int, float)):
            cap_note = "; high power ceiling"
        bitrate = settings.get("minBitrate")
        width = settings.get("channelWidth")
        channels = settings.get("validAutoChannels")
        details = []
        if isinstance(bitrate, (int, float)):
            details.append(f"{float(bitrate):.0f} Mbps min bitrate")
        if width:
            details.append(f"{width} channel width")
        if isinstance(channels, list) and channels:
            details.append(f"{len(channels)} auto channel(s)")
        name = profile.get("name") or "Unnamed RF profile"
        source = "exact AP assignment" if exact else "default/profile fallback"
        suffix = f"; {', '.join(details)}" if details else ""
        return f"Current RF profile: {name} ({source}); {min_text}; {max_text}{cap_note}{suffix}"

    def _power_context(ap: Dict[str, Any], band: str) -> str:
        net_id = ap["network_id"]
        assignment = assignment_by_serial.get(ap["serial"])
        assigned_profile = assignment.get("rfProfile") if isinstance(assignment, dict) else None
        assigned_profile_id = str(assigned_profile.get("id")) if isinstance(assigned_profile, dict) and assigned_profile.get("id") else ""
        if assigned_profile_id:
            profile = _profile_settings_by_id(net_id).get(assigned_profile_id)
            if profile:
                return _format_profile_power(profile, band, exact=True)
            if isinstance(assigned_profile, dict):
                return f"Current RF profile: {assigned_profile.get('name') or assigned_profile_id} (exact AP assignment); settings detail not in backup"

        profiles = rf_profiles.get(net_id) if isinstance(rf_profiles, dict) else None
        if not isinstance(profiles, list) or not profiles:
            return "RF profile power not available"
        default_profiles = [
            profile for profile in profiles
            if isinstance(profile, dict) and (profile.get("isIndoorDefault") or profile.get("isOutdoorDefault"))
        ]
        if len(default_profiles) == 1:
            return _format_profile_power(default_profiles[0], band, exact=False)
        band_map = {
            "2.4": "twoFourGhzSettings",
            "5": "fiveGhzSettings",
            "6": "sixGhzSettings",
        }
        field = band_map.get(str(band))
        values = []
        names = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            settings = profile.get(field) if field else None
            if isinstance(settings, dict):
                min_power = settings.get("minPower")
                max_power = settings.get("maxPower")
                if isinstance(min_power, (int, float)) or isinstance(max_power, (int, float)):
                    values.append((min_power, max_power))
            if profile.get("name"):
                names.append(str(profile.get("name")))
        if not values:
            return "RF profile power not available"
        min_values = [float(v[0]) for v in values if isinstance(v[0], (int, float))]
        max_values = [float(v[1]) for v in values if isinstance(v[1], (int, float))]
        min_text = f"{min(min_values):.0f}-{max(min_values):.0f} dBm min" if min_values else "min n/a"
        max_text = f"{min(max_values):.0f}-{max(max_values):.0f} dBm max" if max_values else "max n/a"
        cap_note = ""
        if max_values and max(max_values) <= 17:
            cap_note = "; low power ceiling"
        elif max_values and max(max_values) <= 22:
            cap_note = "; moderate power ceiling"
        elif max_values:
            cap_note = "; high power ceiling"
        profile_note = f" across {len(values)} RF profile(s)"
        if names:
            profile_note += f": {', '.join(names[:2])}{'…' if len(names) > 2 else ''}"
        return f"RF profile range; {min_text}; {max_text}{cap_note}{profile_note}"

    def _profile_name(ap: Dict[str, Any]) -> str:
        assignment = assignment_by_serial.get(ap["serial"])
        profile = assignment.get("rfProfile") if isinstance(assignment, dict) else None
        if isinstance(profile, dict) and profile.get("name"):
            return str(profile.get("name"))
        return "Profile assignment not captured"

    def _client_stats(serial: str, net_id: str) -> Dict[str, int]:
        for item in wireless_stats.get(net_id, []) if isinstance(wireless_stats, dict) else []:
            if isinstance(item, dict) and item.get("serial") == serial:
                conn = item.get("connectionStats") or {}
                return {
                    "assoc": int(conn.get("assoc") or 0),
                    "auth": int(conn.get("auth") or 0),
                    "success": int(conn.get("success") or 0),
                }
        return {"assoc": 0, "auth": 0, "success": 0}

    util_by_serial = {
        row.get("serial"): row
        for row in channel_util
        if isinstance(channel_util, list) and isinstance(row, dict) and row.get("serial")
    }
    ap_records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for net_id, net_data in devices_by_network.items():
        for dev in net_data.get("devices", []):
            if not isinstance(dev, dict) or dev.get("productType") != "wireless":
                continue
            serial = str(dev.get("serial") or "")
            if not serial:
                continue
            seen.add(serial)
            util = util_by_serial.get(serial) or {}
            bands = _band_stats(util) if util else {}
            worst_band = ""
            worst_stats: Dict[str, float] | None = None
            if bands:
                worst_band, worst_stats = max(
                    bands.items(),
                    key=lambda item: (
                        item[1].get("wifi", 0.0),
                        item[1].get("total", 0.0),
                        item[1].get("non_wifi", 0.0),
                    ),
                )
            bubble_label, bubble_cls = _bubble(worst_stats)
            clients = _client_stats(serial, net_id)
            ap_records.append(
                {
                    "site": net_data.get("name") or "Unassigned",
                    "network_id": net_id,
                    "name": dev.get("name") or serial,
                    "serial": serial,
                    "model": dev.get("model") or "",
                    "status": dev.get("status") or "unknown",
                    "bands": bands,
                    "worst_band": worst_band,
                    "worst_stats": worst_stats,
                    "bubble": bubble_label,
                    "bubble_cls": bubble_cls,
                    "clients": clients,
                }
            )

    for serial, util in util_by_serial.items():
        if serial in seen:
            continue
        net_id = (util.get("network") or {}).get("id") or "unassigned"
        net_data = devices_by_network.get(net_id, {"name": "Unassigned"})
        bands = _band_stats(util)
        worst_band, worst_stats = ("", None)
        if bands:
            worst_band, worst_stats = max(
                bands.items(),
                key=lambda item: (
                    item[1].get("wifi", 0.0),
                    item[1].get("total", 0.0),
                    item[1].get("non_wifi", 0.0),
                ),
            )
        bubble_label, bubble_cls = _bubble(worst_stats)
        ap_records.append(
            {
                "site": net_data.get("name") or "Unassigned",
                "network_id": net_id,
                "name": serial,
                "serial": serial,
                "model": "",
                "status": "unknown",
                "bands": bands,
                "worst_band": worst_band,
                "worst_stats": worst_stats,
                "bubble": bubble_label,
                "bubble_cls": bubble_cls,
                "clients": _client_stats(serial, net_id),
            }
        )

    if not ap_records:
        return """
    <section id="ap-spectrum" class="report-section">
      <h2>AP Spectrum Availability &amp; Interference Report</h2>
      <div class="summary-card"><div class="summary-body">No wireless AP inventory was available for a dedicated RF report.</div></div>
    </section>
        """

    with_telemetry = [ap for ap in ap_records if ap["bands"]]
    high_pressure = [ap for ap in with_telemetry if "Too close" in ap["bubble"] or "WAY TOO CLOSE" in ap["bubble"]]
    tight_pressure = [ap for ap in with_telemetry if "Tight" in ap["bubble"]]
    noise_pressure = [ap for ap in with_telemetry if "non-Wi-Fi" in ap["bubble"] or "External RF" in ap["bubble"]]
    no_telemetry = [ap for ap in ap_records if not ap["bands"]]
    site_counts: Dict[str, Dict[str, int]] = {}
    for ap in ap_records:
        site = site_counts.setdefault(ap["site"], {"aps": 0, "high": 0, "tight": 0, "missing": 0})
        site["aps"] += 1
        if ap in high_pressure:
            site["high"] += 1
        if ap in tight_pressure:
            site["tight"] += 1
        if not ap["bands"]:
            site["missing"] += 1

    site_rows = "".join(
        "<tr>"
        f"<td>{_he(site)}</td>"
        f"<td>{counts['aps']}</td>"
        f"<td>{counts['high']}</td>"
        f"<td>{counts['tight']}</td>"
        f"<td>{counts['missing']}</td>"
        "</tr>"
        for site, counts in sorted(site_counts.items())
    )

    def _candidate_rows(ap: Dict[str, Any]) -> str:
        band = ap["worst_band"]
        stats = ap["worst_stats"] or {}
        if stats.get("non_wifi", 0.0) >= 25 and stats.get("wifi", 0.0) < 40:
            return (
                '<tr><td colspan="6" class="empty-state">'
                "Worst symptom is non-Wi-Fi interference, not AP-to-AP overlap. Inspect local RF noise sources, "
                "run Dashboard RF Spectrum or a site survey, and avoid removing APs solely from this signal."
                "</td></tr>"
            )
        candidates = []
        for other in ap_records:
            if other["serial"] == ap["serial"] or other["network_id"] != ap["network_id"] or not band:
                continue
            stats = other["bands"].get(band)
            if not stats:
                continue
            bubble, cls = _bubble(stats)
            candidates.append((stats.get("wifi", 0.0), stats.get("total", 0.0), other, stats, bubble, cls))
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]["name"]))
        if not candidates:
            return '<tr><td colspan="6" class="empty-state">No same-site AP telemetry candidates were available for this affected band.</td></tr>'
        rows = []
        for _, __, other, stats, bubble, cls in candidates[:6]:
            if "External RF" in bubble or "non-Wi-Fi" in bubble:
                context = "Same-band noise observation; not AP overlap"
            elif "Too close" in bubble or "WAY TOO CLOSE" in bubble:
                context = "Likely overlap candidate"
            else:
                context = "Within same RF domain; verify on floor plan"
            rows.append(
                "<tr>"
                f"<td>{_he(other['name'])}<br><code>{_he(other['serial'])}</code></td>"
                f"<td>{_he(other['model'] or 'Unknown')}</td>"
                f"<td>{_he(band)} GHz</td>"
                f"<td>{stats['wifi']:.1f}% Wi-Fi / {stats['total']:.1f}% total</td>"
                f"<td><span class=\"{cls}\">{_he(bubble)}</span></td>"
                f"<td>{_he(context)}</td>"
                "</tr>"
            )
        return "".join(rows)

    def _band_rows(ap: Dict[str, Any]) -> str:
        if not ap["bands"]:
            return '<tr><td colspan="6" class="empty-state">No per-band channel utilization was returned for this AP.</td></tr>'
        rows = []
        for band, stats in sorted(ap["bands"].items(), key=lambda item: item[0]):
            bubble, cls = _bubble(stats)
            rows.append(
                "<tr>"
                f"<td>{_he(band)} GHz</td>"
                f"<td>{stats['wifi']:.1f}%</td>"
                f"<td>{stats['non_wifi']:.1f}%</td>"
                f"<td>{stats['total']:.1f}%</td>"
                f"<td><span class=\"{cls}\">{_he(bubble)}</span></td>"
                f"<td>{_he(_power_context(ap, band))}</td>"
                "</tr>"
            )
        return "".join(rows)

    def _recommendation(ap: Dict[str, Any]) -> str:
        stats = ap["worst_stats"] or {}
        power = _power_context(ap, ap["worst_band"])
        if stats.get("non_wifi", 0.0) >= 50:
            return (
                "Treat this as an external RF noise problem before changing AP density. "
                "Use Meraki RF Spectrum or a field survey to identify local interferers, then retest channel utilization. "
                "Do not remove or replace APs solely because this band is saturated by non-Wi-Fi energy. "
                + power
            )
        if stats.get("non_wifi", 0.0) >= 25:
            return (
                "Prioritize finding the local non-Wi-Fi interference source. Replacement APs will still share the same noisy spectrum until that source is removed or avoided. "
                + power
            )
        if "WAY TOO CLOSE" in ap["bubble"]:
            return (
                "Treat this as a high-priority RF density problem. If the floor plan confirms "
                "another AP is physically close, remove, disable, or relocate one AP before "
                "adding replacement Wi-Fi 6/7 hardware. "
                + power
            )
        if "Too close" in ap["bubble"]:
            return (
                "Review nearby AP placement, channel reuse, and transmit power. If this AP is already "
                "running under a reduced power profile, removal or relocation is more likely to help "
                "than increasing power. "
                + power
            )
        if stats.get("non_wifi", 0.0) >= 15:
            return "Inspect for non-Wi-Fi noise sources near this AP before replacing hardware. New APs will still share the same noisy spectrum. " + power
        if not ap["bands"]:
            return "Re-run the backup after the AP is online and reporting channel utilization; no RF decision should be made from missing telemetry alone."
        return "No immediate removal recommendation from current telemetry. Keep this AP in the upgrade plan unless the floor plan shows unnecessary overlap. " + power

    def _priority_action(ap: Dict[str, Any]) -> str:
        stats = ap["worst_stats"] or {}
        power = _power_context(ap, ap["worst_band"])
        if stats.get("non_wifi", 0.0) >= 25:
            return "Find/remove RF noise source; retest before AP replacement. " + power
        if "WAY TOO CLOSE" in ap["bubble"]:
            return "Validate floor plan; remove, disable, or relocate one AP if physical overlap is confirmed. " + power
        if "Too close" in ap["bubble"]:
            return "Tune channel reuse and power; consider relocation/removal if profile is already constrained. " + power
        if "Tight" in ap["bubble"]:
            return "Tune profile/channel width before one-for-one refresh. " + power
        return "Monitor; no immediate RF remediation from this telemetry."

    priority_rows = "".join(
        "<tr>"
        f"<td>{_he(ap['site'])}</td>"
        f"<td>{_he(ap['name'])}<br><code>{_he(ap['serial'])}</code></td>"
        f"<td>{_he((ap['worst_band'] + ' GHz') if ap['worst_band'] else 'No data')}</td>"
        f"<td><span class=\"{ap['bubble_cls']}\">{_he(ap['bubble'])}</span></td>"
        f"<td>{_he(_profile_name(ap))}</td>"
        f"<td>{_he(_priority_action(ap))}</td>"
        "</tr>"
        for ap in sorted(
            [*high_pressure, *noise_pressure, *tight_pressure],
            key=lambda item: (
                {"check-fail": 0, "check-warning": 1, "check-pass": 2}.get(item["bubble_cls"], 3),
                item["site"],
                item["name"],
            ),
        )[:18]
    )
    if not priority_rows:
        priority_rows = '<tr><td colspan="6" class="empty-state">No APs require immediate RF remediation from this telemetry window.</td></tr>'

    ap_pages = []
    for ap in sorted(
        ap_records,
        key=lambda item: (
            {"check-fail": 0, "check-warning": 1, "check-pass": 2}.get(item["bubble_cls"], 3),
            item["site"],
            item["name"],
        ),
    ):
        stats = ap["worst_stats"] or {}
        clients = ap["clients"]
        ap_pages.append(
            f"""
    <section class="report-section ap-unit-page">
      <h2>{_he(ap['name'])}</h2>
      <p><strong>{_he(ap['site'])}</strong> &nbsp;|&nbsp; <code>{_he(ap['serial'])}</code> &nbsp;|&nbsp; {_he(ap['model'] or 'Unknown model')} &nbsp;|&nbsp; status: {_he(ap['status'])}</p>
      <div class="kpi-row">
        <div class="kpi"><div class="kpi-label">RF Bubble</div><div class="kpi-value"><span class="{ap['bubble_cls']}">{_he(ap['bubble'])}</span></div><div class="kpi-note">Inferred from airtime telemetry</div></div>
        <div class="kpi"><div class="kpi-label">Worst Band</div><div class="kpi-value">{_he((ap['worst_band'] + ' GHz') if ap['worst_band'] else 'No data')}</div><div class="kpi-note">{stats.get('total', 0.0):.1f}% total utilization</div></div>
        <div class="kpi"><div class="kpi-label">Wi-Fi Airtime</div><div class="kpi-value">{stats.get('wifi', 0.0):.1f}%</div><div class="kpi-note">Co-channel / neighbor pressure signal</div></div>
        <div class="kpi"><div class="kpi-label">Client Events</div><div class="kpi-value">{clients['assoc']} assoc</div><div class="kpi-note">{clients['auth']} auth / {clients['success']} success</div></div>
      </div>
      <div class="summary-card">
        <div class="summary-title">Assessment</div>
        <div class="summary-body">
          This page estimates RF pressure from Meraki channel-utilization data. It does not claim measured physical distance; AP-to-AP overlap is inferred from Wi-Fi airtime, while non-Wi-Fi utilization is treated as external RF noise that should be investigated separately.
        </div>
      </div>
      <table class="data dense">
        <thead><tr><th>Band</th><th>Wi-Fi</th><th>Non-Wi-Fi</th><th>Total</th><th>Bubble</th><th>Transmit Power Context</th></tr></thead>
        <tbody>{_band_rows(ap)}</tbody>
      </table>
      <h3>Same-Band Context / Overlap Candidates</h3>
      <table class="data dense">
        <thead><tr><th>Nearby AP Candidate</th><th>Model</th><th>Band</th><th>Candidate Airtime</th><th>Bubble</th><th>Context</th></tr></thead>
        <tbody>{_candidate_rows(ap)}</tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">Recommendation</div>
        <div class="summary-body">{_he(_recommendation(ap))}</div>
      </div>
    </section>
            """
        )

    return f"""
    <section id="ap-spectrum" class="report-section">
      <h2>AP Spectrum Availability &amp; Interference Report</h2>
      <p>This dedicated RF report is designed for wireless refresh planning. It identifies APs whose spectrum is clean, APs that are merely within useful range of other radios, APs whose Wi-Fi airtime suggests tight or excessive overlap, and APs whose non-Wi-Fi utilization points to external RF noise. Excessive overlap or unresolved noise can reduce throughput, increase retries, slow roaming, and make a Wi-Fi 6/7 replacement look worse than it should if density, power, and noise sources are not corrected first.</p>
      <div class="kpi-row">
        <div class="kpi"><div class="kpi-label">AP Pages</div><div class="kpi-value">{len(ap_records)}</div><div class="kpi-note">One page per AP unit</div></div>
        <div class="kpi"><div class="kpi-label">RF Telemetry</div><div class="kpi-value">{len(with_telemetry)}</div><div class="kpi-note">APs with channel utilization</div></div>
        <div class="kpi"><div class="kpi-label">Too Close</div><div class="kpi-value">{len(high_pressure)}</div><div class="kpi-note">High co-channel pressure</div></div>
        <div class="kpi"><div class="kpi-label">RF Noise</div><div class="kpi-value">{len(noise_pressure)}</div><div class="kpi-note">Non-Wi-Fi interference</div></div>
        <div class="kpi"><div class="kpi-label">Missing Data</div><div class="kpi-value">{len(no_telemetry)}</div><div class="kpi-note">Offline/dormant/no channel data</div></div>
      </div>
      <table class="data">
        <thead><tr><th>Site</th><th>APs</th><th>Too Close</th><th>Tight Bubble</th><th>No Telemetry</th></tr></thead>
        <tbody>{site_rows}</tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">How To Read The Bubble Scale</div>
        <div class="summary-body">
          Clean bubble means no current overlap symptom. Within range means normal overlap for roaming. Tight bubble means tune channel/power/placement. Too close and WAY TOO CLOSE mean AP-to-AP overlap should be reviewed before adding or replacing APs. RF Noise means non-Wi-Fi energy is saturating the band; find the external source before removing APs.
        </div>
      </div>
      <h3>Recommended RF Work Queue</h3>
      <table class="data dense">
        <thead><tr><th>Site</th><th>AP</th><th>Band</th><th>Symptom</th><th>RF Profile</th><th>Guidance</th></tr></thead>
        <tbody>{priority_rows}</tbody>
      </table>
    </section>
    {''.join(ap_pages)}
    """


def _build_wan_capacity_section(
    uplink_statuses: Any,
    appliance_uplinks_usage: Any,
    devices_avail: List[Dict[str, Any]],
    networks_by_id: Dict[str, Dict[str, Any]],
) -> str:
    if not isinstance(uplink_statuses, list) or not uplink_statuses:
        return """
    <section id="wan-capacity" class="report-section">
      <h2>13. Internet Capacity &amp; Utilization</h2>
      <div class="summary-card"><div class="summary-body">No WAN uplink telemetry was available in this backup.</div></div>
    </section>
    """

    device_by_serial = {
        d.get("serial"): d for d in devices_avail if isinstance(d, dict) and d.get("serial")
    }
    rows = []
    summary = {"active": 0, "ready": 0, "failed": 0, "unknown_speed": 0}
    recommendation_flags = {"missing_speed": False, "degraded": False}
    usage_by_network = appliance_uplinks_usage if isinstance(appliance_uplinks_usage, dict) else {}
    for device in uplink_statuses:
        if not isinstance(device, dict):
            continue
        serial = device.get("serial")
        dev = device_by_serial.get(serial, {})
        net_id = device.get("networkId") or ((dev.get("network") or {}).get("id"))
        site_name = (networks_by_id.get(net_id) or {}).get("name") or "Unassigned"
        for uplink in device.get("uplinks", []) or []:
            if not isinstance(uplink, dict):
                continue
            status = str(uplink.get("status") or "unknown").lower()
            speed = uplink.get("speed")
            interface = uplink.get("interface") or "wan"
            if status == "active":
                summary["active"] += 1
            elif status == "ready":
                summary["ready"] += 1
            else:
                summary["failed"] += 1
                recommendation_flags["degraded"] = True
            if not speed:
                summary["unknown_speed"] += 1
                recommendation_flags["missing_speed"] = True
            max_capacity = str(speed or "Unknown")
            net_usage = usage_by_network.get(net_id)
            series = []
            if isinstance(net_usage, list):
                for point in net_usage:
                    if not isinstance(point, dict):
                        continue
                    if str(point.get("interface") or "").lower() != str(interface).lower():
                        continue
                    recv = float(point.get("receivedKbps") or 0)
                    sent = float(point.get("sentKbps") or 0)
                    series.append(recv + sent)
            sustain = "Current snapshot only"
            peak = "Historical peak not collected"
            freq = "Usage frequency unavailable"
            avg_usage = 0.0
            peak_usage = 0.0
            busy_samples = 0
            if series:
                avg_usage = sum(series) / len(series)
                peak_usage = max(series)
                busy_samples = sum(1 for value in series if value >= max(peak_usage * 0.7, 1))
                sustain = f"{avg_usage:.0f} Kbps avg over 7d"
                peak = f"{peak_usage:.0f} Kbps peak"
                freq = f"{busy_samples}/{len(series)} samples near peak"
            score = 100 if status == "active" else 72 if status == "ready" else 28
            rows.append(
                {
                    "site": site_name,
                    "device": dev.get("name") or serial or "MX",
                    "model": device.get("model") or dev.get("model") or "",
                    "interface": interface,
                    "status": status.title(),
                    "public_ip": uplink.get("publicIp") or uplink.get("ip") or "—",
                    "max_capacity": max_capacity,
                    "sustain": sustain,
                    "peak": peak,
                    "frequency": freq,
                    "score": score,
                    "avg_usage": avg_usage,
                    "peak_usage": peak_usage,
                }
            )

    rows.sort(key=lambda item: (item["site"], item["device"], item["interface"]))
    graph_rows = "".join(
        "<div class=\"wan-capacity-row\">"
        f"<div class=\"wan-capacity-label\">{_he(item['site'])} / {_he(item['device'])} / {_he(item['interface'])}</div>"
        f"<div class=\"wan-capacity-bar\"><span style=\"width:{item['score']}%\"></span></div>"
        f"<div class=\"wan-capacity-meta\">{_he(item['status'])} &middot; Max speed: {_he(item['max_capacity'])}</div>"
        "</div>"
        for item in rows
    )
    table_rows = "".join(
        "<tr>"
        f"<td>{_he(item['site'])}</td>"
        f"<td>{_he(item['device'])}<br><code>{_he(item['model'])}</code></td>"
        f"<td>{_he(item['interface'])}</td>"
        f"<td>{_he(item['status'])}</td>"
        f"<td>{_he(item['max_capacity'])}</td>"
        f"<td>{_he(item['sustain'])}</td>"
        f"<td>{_he(item['peak'])}</td>"
        f"<td>{_he(item['frequency'])}</td>"
        f"<td>{_he(item['public_ip'])}</td>"
        "</tr>"
        for item in rows
    )
    recommendations = []
    if recommendation_flags["missing_speed"]:
        recommendations.append("The current backup contains uplink state but not negotiated or subscribed WAN bandwidth for one or more circuits. Add Meraki appliance uplink usage/history endpoints so the report can show sustained throughput and true peak demand.")
    if not any(item["peak_usage"] > 0 for item in rows):
        recommendations.append("WAN usage history is still absent or empty for these circuits. Validate the new appliance uplink usage history collection path and confirm the Meraki org/network supports that endpoint.")
    if recommendation_flags["degraded"]:
        recommendations.append("At least one WAN uplink is not active. Review failover policy, ISP health, and MX uplink preferences before release.")
    if not recommendations:
        recommendations.append("WAN links appear healthy in the current snapshot. To validate circuit sizing, add historical usage collection so peak and sustained demand can be compared against contracted bandwidth.")

    return f"""
    <section id="wan-capacity" class="report-section">
      <h2>13. Internet Capacity &amp; Utilization</h2>
      <p>This section summarizes current MX WAN uplink state and the maximum internet capacity exposed by the current backup. When Meraki uplink usage history is available, the report also estimates sustained load, observed peak load, and how frequently the circuit approaches its own observed peak during the sampled period.</p>
      <div class="summary-card">
        <div class="summary-title">WAN Snapshot</div>
        <div class="summary-body">
          Active uplinks: <strong>{summary['active']}</strong> &nbsp;|&nbsp;
          Warm standby / ready: <strong>{summary['ready']}</strong> &nbsp;|&nbsp;
          Degraded / other: <strong>{summary['failed']}</strong> &nbsp;|&nbsp;
          Unknown speed circuits: <strong>{summary['unknown_speed']}</strong>
        </div>
      </div>
      <div class="wan-capacity-chart">{graph_rows}</div>
      <table class="data">
        <thead>
          <tr>
            <th>Site</th><th>MX / Uplink</th><th>Interface</th><th>Status</th><th>Max Capacity</th>
            <th>Sustained Load</th><th>Peak Load</th><th>Usage Frequency</th><th>Public IP</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">What Is Missing For True Capacity Planning</div>
        <div class="summary-body"><ul>{''.join(f'<li>{_he(item)}</li>' for item in recommendations)}</ul></div>
      </div>
    </section>
    """


def _build_config_coverage_section(
    org_dir: str,
    networks: List[Dict[str, Any]],
) -> str:
    org_checks = [
        ("Switch Port Configs", "switch_port_configs.json"),
        ("Switch Port Statuses", "switch_port_statuses.json"),
        ("LLDP/CDP Neighbors", "lldp_cdp.json"),
        ("Wireless Settings", "wireless_settings.json"),
        ("Wireless SSIDs", "wireless_ssids.json"),
        ("Wireless RF Profiles", "wireless_rf_profiles.json"),
        ("Network Clients", "network_clients.json"),
        ("Appliance Uplink Usage", "appliance_uplinks_usage.json"),
        ("Appliance VLANs", "appliance_vlans.json"),
        ("Appliance DHCP Subnets", "appliance_dhcp_subnets.json"),
        ("Appliance Policy Backup", "appliance_policy_backup.json"),
        ("Security Baseline Summary", "security_baseline.json"),
        ("Licensing", "licensing.json"),
        ("Firmware Upgrades", "firmware_upgrades.json"),
    ]
    org_rows = []
    for label, filename in org_checks:
        status = "Present" if os.path.exists(os.path.join(org_dir, filename)) else "Missing"
        org_rows.append([label, status])

    network_rows = []
    for net in sorted(networks, key=lambda item: item.get("name") or ""):
        net_id = net.get("id")
        if not net_id:
            continue
        net_name = net.get("name") or net_id
        base = os.path.join(org_dir, "networks", net_id)
        def _has(name: str) -> str:
            return "Present" if os.path.exists(os.path.join(base, name)) else "Missing"
        is_appliance_network = "appliance" in (net.get("productTypes") or [])
        if not is_appliance_network:
            network_rows.append(
                [
                    net_name,
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                    _has("network_clients.json"),
                ]
            )
            continue
        network_rows.append(
            [
                net_name,
                _has("appliance_firewall_settings.json"),
                _has("appliance_port_forwarding_rules.json"),
                _has("appliance_intrusion.json"),
                _has("appliance_malware.json"),
                _has("appliance_vlans.json"),
                _has("appliance_policy_backup.json"),
                _has("network_clients.json"),
            ]
        )

    return f"""
    <section id="config-coverage" class="report-section">
      <h2>11. Configuration Backup Coverage</h2>
      <p>This section documents which configuration artifacts are present in the current backup set. Missing items indicate API collection gaps or inaccessible product scopes that should be added before final audit sign-off.</p>
      {render_section("Org-Wide Configuration Artifacts", [["Artifact", "Status"]] + org_rows if org_rows else [])}
      {render_section("Per-Network Appliance Configuration", [["Network", "Firewall Settings", "Port Forwarding", "IDS/IPS", "AMP/Malware", "VLANs/DHCP", "Policy Backup", "Client Detail"]] + network_rows if network_rows else [])}
    </section>
    """


def _policy_rules(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("rules"), list):
        return [rule for rule in payload.get("rules", []) if isinstance(rule, dict)]
    if isinstance(payload, list):
        return [rule for rule in payload if isinstance(rule, dict)]
    return []


def _policy_error(payload: Any) -> str:
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload.get("error"))
    return ""


def _content_filter_summary(payload: Any) -> Tuple[int, int, int, str]:
    if not isinstance(payload, dict) or payload.get("error"):
        return (0, 0, 0, "")
    blocked = payload.get("blockedUrlCategories") or []
    allowed = payload.get("allowedUrlPatterns") or []
    blocked_patterns = payload.get("blockedUrlPatterns") or []
    url_categories = ", ".join(
        str((item or {}).get("name") or item)
        for item in blocked[:8]
    )
    if len(blocked) > 8:
        url_categories += f" +{len(blocked) - 8} more"
    return (
        len(blocked) if isinstance(blocked, list) else 0,
        len(allowed) if isinstance(allowed, list) else 0,
        len(blocked_patterns) if isinstance(blocked_patterns, list) else 0,
        url_categories,
    )


def _build_appliance_policy_section(
    networks: List[Dict[str, Any]],
    appliance_policy_backup: Dict[str, Any],
) -> str:
    network_names = {
        n.get("id"): n.get("name") or n.get("id")
        for n in networks
        if isinstance(n, dict) and n.get("id")
    }
    if not appliance_policy_backup:
        return """
      <h3>MX Firewall, Filtering &amp; Policy Backup</h3>
      <div class="summary-card">
        <div class="summary-body">
          No MX firewall/content-filtering policy backup was present in this backup. Re-run collection
          with <code>appliance_policy_backup.json</code> enabled to print L3/L7 firewall rules,
          inbound rules, NAT, content filtering, traffic shaping, VPN, group policies, and syslog.
        </div>
      </div>
        """

    summary_rows = []
    rule_rows = []
    error_rows = []
    l3_total = l7_total = inbound_total = nat_total = forwarding_total = 0
    content_category_total = 0
    content_allow_total = 0
    content_block_total = 0
    rule_limit = 80
    displayed = 0

    def _add_rule_row(net_name: str, family: str, rule: Dict[str, Any]) -> None:
        nonlocal displayed
        if displayed >= rule_limit:
            return
        displayed += 1
        source = rule.get("srcCidr") or rule.get("srcPort") or rule.get("allowedIps") or "Any"
        destination = (
            rule.get("destCidr")
            or rule.get("destPort")
            or rule.get("lanIp")
            or rule.get("value")
            or rule.get("publicIp")
            or "Any"
        )
        ports = rule.get("destPort") or rule.get("publicPort") or rule.get("localPort") or rule.get("port") or "Any"
        rule_rows.append(
            "<tr>"
            f"<td>{_he(net_name)}</td>"
            f"<td>{_he(family)}</td>"
            f"<td>{_he(rule.get('policy') or rule.get('protocol') or rule.get('type') or 'Rule')}</td>"
            f"<td>{_he(source)}</td>"
            f"<td>{_he(destination)}</td>"
            f"<td>{_he(ports)}</td>"
            f"<td>{_he(rule.get('comment') or rule.get('name') or '—')}</td>"
            "</tr>"
        )

    for net_id, payload in sorted(appliance_policy_backup.items(), key=lambda item: network_names.get(item[0], item[0])):
        net_name = network_names.get(net_id, net_id)
        if isinstance(payload, dict) and payload.get("error"):
            error_rows.append(
                f"<tr><td>{_he(net_name)}</td><td colspan=\"6\" class=\"empty-state\">{_he(str(payload.get('error'))[:180])}</td></tr>"
            )
            continue
        if not isinstance(payload, dict):
            continue

        l3_rules = _policy_rules(payload.get("l3FirewallRules"))
        l7_rules = _policy_rules(payload.get("l7FirewallRules"))
        inbound_rules = _policy_rules(payload.get("inboundFirewallRules"))
        port_forwarding = _policy_rules(payload.get("portForwardingRules"))
        nat_1_1 = _policy_rules(payload.get("oneToOneNatRules"))
        nat_1_many = _policy_rules(payload.get("oneToManyNatRules"))
        group_policies = payload.get("groupPolicies") if isinstance(payload.get("groupPolicies"), list) else []
        syslog_servers = payload.get("syslogServers", {})
        syslog_count = len(syslog_servers.get("servers") or []) if isinstance(syslog_servers, dict) else 0
        vpn = payload.get("siteToSiteVpn") if isinstance(payload.get("siteToSiteVpn"), dict) else {}
        vpn_mode = vpn.get("mode") or "not captured"
        cats, allows, blocks, cat_names = _content_filter_summary(payload.get("contentFiltering"))

        l3_total += len(l3_rules)
        l7_total += len(l7_rules)
        inbound_total += len(inbound_rules)
        forwarding_total += len(port_forwarding)
        nat_total += len(nat_1_1) + len(nat_1_many)
        content_category_total += cats
        content_allow_total += allows
        content_block_total += blocks

        summary_rows.append(
            "<tr>"
            f"<td>{_he(net_name)}</td>"
            f"<td>{len(l3_rules)}</td>"
            f"<td>{len(l7_rules)}</td>"
            f"<td>{len(inbound_rules)}</td>"
            f"<td>{len(port_forwarding)}</td>"
            f"<td>{len(nat_1_1) + len(nat_1_many)}</td>"
            f"<td>{cats} cat / {allows} allow / {blocks} block</td>"
            f"<td>{len(group_policies)}</td>"
            f"<td>{_he(str(vpn_mode))}</td>"
            f"<td>{syslog_count}</td>"
            "</tr>"
        )

        for family, rules in (
            ("L3", l3_rules),
            ("L7", l7_rules),
            ("Inbound", inbound_rules),
            ("Port Forward", port_forwarding),
            ("1:1 NAT", nat_1_1),
            ("1:Many NAT", nat_1_many),
        ):
            for rule in rules:
                _add_rule_row(net_name, family, rule)
        if cat_names:
            rule_rows.append(
                "<tr>"
                f"<td>{_he(net_name)}</td><td>Content Filter</td><td>Blocked Categories</td>"
                f"<td colspan=\"4\">{_he(cat_names)}</td>"
                "</tr>"
            )

        for key, item in payload.items():
            err = _policy_error(item)
            if err:
                error_rows.append(
                    "<tr>"
                    f"<td>{_he(net_name)}</td><td>{_he(key)}</td>"
                    f"<td colspan=\"5\" class=\"empty-state\">{_he(err[:180])}</td>"
                    "</tr>"
                )

    omitted_note = ""
    if displayed >= rule_limit:
        omitted_note = (
            f"<p class=\"muted\">Rule table capped at {rule_limit} rows for report readability. "
            "The JSON backup contains the full policy export.</p>"
        )

    return f"""
      <h3>MX Firewall, Filtering &amp; Policy Backup</h3>
      <div class="summary-card">
        <div class="summary-title">Policy Collection Summary</div>
        <div class="summary-body">
          L3 rules: <strong>{l3_total}</strong>.
          L7 rules: <strong>{l7_total}</strong>.
          Inbound rules: <strong>{inbound_total}</strong>.
          Port forwards: <strong>{forwarding_total}</strong>.
          NAT mappings: <strong>{nat_total}</strong>.
          Content filter customizations: <strong>{content_category_total}</strong> blocked categories,
          <strong>{content_allow_total}</strong> allowed URL patterns,
          <strong>{content_block_total}</strong> blocked URL patterns.
        </div>
      </div>
      <h4>Policy Backup by Network</h4>
      <table class="data dense">
        <thead>
          <tr><th>Network</th><th>L3</th><th>L7</th><th>Inbound</th><th>Fwd</th><th>NAT</th><th>Content Filtering</th><th>Groups</th><th>VPN</th><th>Syslog</th></tr>
        </thead>
        <tbody>{''.join(summary_rows) if summary_rows else '<tr><td colspan="10" class="empty-state">No MX policy records were present.</td></tr>'}</tbody>
      </table>
      <h4>Printable Firewall &amp; NAT Rule Snapshot</h4>
      <table class="data dense">
        <thead>
          <tr><th>Network</th><th>Policy</th><th>Action / Type</th><th>Source</th><th>Destination</th><th>Ports</th><th>Comment / Name</th></tr>
        </thead>
        <tbody>{''.join(rule_rows + error_rows) if (rule_rows or error_rows) else '<tr><td colspan="7" class="empty-state">No firewall, NAT, or content-filtering rows were present.</td></tr>'}</tbody>
      </table>
      {omitted_note}
        """


def _build_addressing_dhcp_section(
    networks: List[Dict[str, Any]],
    appliance_vlans_by_network: Dict[str, Any],
    appliance_dhcp_subnets_by_serial: Dict[str, Any],
    client_records: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
) -> str:
    network_names = {
        n.get("id"): n.get("name") or n.get("id")
        for n in networks
        if isinstance(n, dict) and n.get("id")
    }
    appliance_names = {
        d.get("serial"): d.get("name") or d.get("model") or d.get("serial")
        for d in devices
        if isinstance(d, dict) and d.get("serial") and d.get("productType") == "appliance"
    }

    client_counts: Dict[Tuple[str, str], int] = {}
    for client in client_records:
        if not isinstance(client, dict):
            continue
        net_id = client.get("networkId") or (client.get("network") or {}).get("id")
        vlan = str(client.get("vlan") or client.get("vlanId") or client.get("namedVlan") or "—")
        if net_id:
            client_counts[(str(net_id), vlan)] = client_counts.get((str(net_id), vlan), 0) + 1

    vlan_rows = []
    vlan_total = 0
    dhcp_enabled = 0
    relay_count = 0
    for net_id, vlans in appliance_vlans_by_network.items() if isinstance(appliance_vlans_by_network, dict) else []:
        if isinstance(vlans, dict) and vlans.get("error"):
            vlan_rows.append(
                "<tr>"
                f"<td>{_he(network_names.get(net_id, net_id))}</td>"
                "<td colspan=\"8\" class=\"empty-state\">"
                f"{_he(str(vlans.get('error'))[:180])}"
                "</td></tr>"
            )
            continue
        if not isinstance(vlans, list):
            continue
        for vlan in sorted(vlans, key=lambda item: str(item.get("id") or item.get("name") or "")):
            if not isinstance(vlan, dict):
                continue
            vlan_total += 1
            handling = str(vlan.get("dhcpHandling") or "Unknown")
            if "run a dhcp server" in handling.lower():
                dhcp_enabled += 1
            if "relay" in handling.lower():
                relay_count += 1
            vlan_id = str(vlan.get("id") or "—")
            clients = client_counts.get((str(net_id), vlan_id), 0)
            vlan_rows.append(
                "<tr>"
                f"<td>{_he(network_names.get(net_id, net_id))}</td>"
                f"<td>{_he(vlan_id)}</td>"
                f"<td>{_he(vlan.get('name') or '—')}</td>"
                f"<td><code>{_he(vlan.get('subnet') or vlan.get('cidr') or '—')}</code></td>"
                f"<td><code>{_he(vlan.get('applianceIp') or '—')}</code></td>"
                f"<td>{_he(handling)}</td>"
                f"<td>{_he(vlan.get('dhcpLeaseTime') or '—')}</td>"
                f"<td>{_he(', '.join(str(ip) for ip in vlan.get('dhcpRelayServerIps') or []) or '—')}</td>"
                f"<td>{clients}</td>"
                "</tr>"
            )

    dhcp_rows = []
    constrained = 0
    for serial, subnets in appliance_dhcp_subnets_by_serial.items() if isinstance(appliance_dhcp_subnets_by_serial, dict) else []:
        if isinstance(subnets, dict) and subnets.get("error"):
            dhcp_rows.append(
                "<tr>"
                f"<td>{_he(appliance_names.get(serial, serial))}<br><code>{_he(serial)}</code></td>"
                "<td colspan=\"5\" class=\"empty-state\">"
                f"{_he(str(subnets.get('error'))[:180])}"
                "</td></tr>"
            )
            continue
        if not isinstance(subnets, list):
            continue
        for subnet in sorted(subnets, key=lambda item: str(item.get("subnet") or "")):
            if not isinstance(subnet, dict):
                continue
            used = int(subnet.get("usedCount") or 0)
            free = int(subnet.get("freeCount") or 0)
            total = used + free
            pct = (used / total * 100) if total else 0.0
            if total and pct >= 80:
                constrained += 1
            cls = "badge-fail" if pct >= 90 else "badge-warn" if pct >= 80 else "badge-ok"
            dhcp_rows.append(
                "<tr>"
                f"<td>{_he(appliance_names.get(serial, serial))}<br><code>{_he(serial)}</code></td>"
                f"<td>{_he(str(subnet.get('vlanId') or '—'))}</td>"
                f"<td><code>{_he(subnet.get('subnet') or '—')}</code></td>"
                f"<td>{used}</td>"
                f"<td>{free}</td>"
                f"<td><span class=\"badge {cls}\">{pct:.1f}% used</span></td>"
                "</tr>"
            )

    if not vlan_rows and not dhcp_rows:
        return """
      <h3>Addressing &amp; DHCP Scope Audit</h3>
      <div class="summary-card">
        <div class="summary-body">
          No MX VLAN or DHCP subnet telemetry was present in this backup. Re-run collection with
          <code>appliance_vlans.json</code> and <code>appliance_dhcp_subnets.json</code> enabled
          to populate subnet, gateway, DHCP handling, relay, lease-time, and pool-utilization data.
        </div>
      </div>
        """

    return f"""
      <h3>Addressing &amp; DHCP Scope Audit</h3>
      <div class="summary-card">
        <div class="summary-title">Addressing Collection Summary</div>
        <div class="summary-body">
          MX VLAN definitions observed: <strong>{vlan_total}</strong>.
          DHCP server VLANs: <strong>{dhcp_enabled}</strong>.
          DHCP relay VLANs: <strong>{relay_count}</strong>.
          DHCP scopes at or above 80% utilization: <strong>{constrained}</strong>.
        </div>
      </div>
      <h4>MX VLAN Interfaces</h4>
      <table class="data dense">
        <thead>
          <tr><th>Network</th><th>VLAN</th><th>Name</th><th>Subnet</th><th>Gateway</th><th>DHCP Mode</th><th>Lease</th><th>Relay Servers</th><th>Clients Seen</th></tr>
        </thead>
        <tbody>{''.join(vlan_rows) if vlan_rows else '<tr><td colspan="9" class="empty-state">No MX VLAN interface definitions were present.</td></tr>'}</tbody>
      </table>
      <h4>DHCP Pool Utilization</h4>
      <table class="data dense">
        <thead>
          <tr><th>Appliance</th><th>VLAN</th><th>Subnet</th><th>Used</th><th>Free</th><th>Utilization</th></tr>
        </thead>
        <tbody>{''.join(dhcp_rows) if dhcp_rows else '<tr><td colspan="6" class="empty-state">No DHCP pool utilization records were present.</td></tr>'}</tbody>
      </table>
        """


def _build_budget_forecast_section(
    inventory_summary: Dict[str, Any],
    pricing_payload: Dict[str, Any],
) -> str:
    by_model = inventory_summary.get("by_model") or {}
    if not by_model:
        return """
    <section id="budget-forecast" class="report-section">
      <h2>12. Hardware Cost &amp; Refresh Plan</h2>
      <div class="summary-card"><div class="summary-body">No model inventory data was available to build a refresh budget.</div></div>
    </section>
    """

    price_map = pricing_payload.get("models") if isinstance(pricing_payload, dict) else None
    if not isinstance(price_map, dict):
        price_map = {}

    rows = []
    missing = []
    total_cost = 0.0
    annual_reserve = 0.0
    for model, count in sorted(by_model.items(), key=lambda item: (-item[1], item[0])):
        price = price_map.get(model, {}).get("unit_cost")
        cycle = price_map.get(model, {}).get("replacement_cycle_years")
        if price is None:
            missing.append(model)
            rows.append([model, str(count), "Missing", "—", "—", "—"])
            continue
        try:
            price = float(price)
        except (ValueError, TypeError):
            missing.append(model)
            rows.append([model, str(count), "Invalid", "—", "—", "—"])
            continue
        ext = price * int(count)
        cycle_years = int(cycle) if isinstance(cycle, (int, float, str)) and str(cycle).isdigit() else 8
        annual = ext / cycle_years if cycle_years else ext / 8
        total_cost += ext
        annual_reserve += annual
        rows.append([model, str(count), f"${price:,.0f}", f"${ext:,.0f}", str(cycle_years), f"${annual:,.0f}"])

    missing_note = ""
    if missing:
        missing_note = (
            "<div class=\"summary-card\"><div class=\"summary-title\">Missing Pricing</div>"
            "<div class=\"summary-body\">Provide pricing.json with unit_cost per model to complete the refresh budget. Missing models: "
            + _he(", ".join(sorted(missing)[:12]))
            + ".</div></div>"
        )

    summary_card = (
        "<div class=\"summary-card\"><div class=\"summary-title\">8-Year Refresh Reserve</div>"
        "<div class=\"summary-body\">"
        f"Estimated total replacement cost: <strong>${total_cost:,.0f}</strong><br>"
        f"Suggested annual reserve (based on replacement cycles): <strong>${annual_reserve:,.0f}</strong>"
        "</div></div>"
    )

    return f"""
    <section id="budget-forecast" class="report-section">
      <h2>12. Hardware Cost &amp; Refresh Plan</h2>
      <p>This section estimates an 8-year hardware refresh reserve using inventory counts and optional pricing inputs. Provide a pricing map to replace placeholders with actual budget numbers.</p>
      {summary_card}
      {render_section("Model Cost Rollup", [["Model", "Count", "Unit Cost", "Extended", "Cycle (yrs)", "Annual Reserve"]] + rows)}
      {missing_note}
    </section>
    """
