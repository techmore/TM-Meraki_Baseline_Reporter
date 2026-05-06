#!/usr/bin/env python3
import argparse
import html
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return [item for item in value["data"] if isinstance(item, dict)]
    return []


def _first(item: Dict[str, Any], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return str(value)
    return default


def _nested(item: Dict[str, Any], path: Iterable[str], default: str = "") -> str:
    cur: Any = item
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return str(cur) if cur not in (None, "") else default


def _device_role(device: Dict[str, Any]) -> str:
    features = {str(feature).lower() for feature in device.get("features", []) if feature}
    raw = " ".join(str(device.get(k, "")) for k in ("type", "model", "modelName", "name", "displayName")).lower()
    if "accesspoint" in features or any(token in raw for token in ("access point", "uap", "u7", "u6", "ap ", "ac pro", "iw hd")):
        return "Access Point"
    if any(token in raw for token in ("gateway", "udm", "uxg", "ucg", "router")):
        return "Gateway"
    if "switching" in features or any(token in raw for token in ("switch", "usw")):
        return "Switch"
    return _first(device, ("type", "productLine", "category"), "Device")


def _status(device: Dict[str, Any]) -> str:
    return _first(device, ("state", "status", "connectionState", "adoptionState"), "unknown")


def _count_by(items: Iterable[Dict[str, Any]], fn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = fn(item) or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _table(headers: List[str], rows: List[List[Any]], empty: str = "No data captured.") -> str:
    if not rows:
        return f"<p class='muted'>{html.escape(empty)}</p>"
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{html.escape(str(cell if cell is not None else ''))}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _summary_cards(cards: List[tuple[str, Any]]) -> str:
    return "<div class='cards'>" + "".join(
        f"<div class='card'><div class='metric'>{html.escape(str(value))}</div><div class='label'>{html.escape(label)}</div></div>"
        for label, value in cards
    ) + "</div>"


def _read_site_file(source: Path, site_summary: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    rel = (site_summary.get("files") or {}).get(key)
    return _items(_load_json(source / rel, [])) if rel else []


def _action_label(policy: Dict[str, Any]) -> str:
    action = policy.get("action")
    if isinstance(action, dict):
        label = str(action.get("type") or "")
        if action.get("allowReturnTraffic") is True:
            label = f"{label} (return allowed)" if label else "return allowed"
        return label
    return str(action or "")


def _zone_label(value: Any, zone_names: Dict[str, str]) -> str:
    if isinstance(value, dict):
        zone_id = str(value.get("zoneId") or "")
        if zone_id:
            return zone_names.get(zone_id, zone_id)
        traffic = value.get("trafficFilter")
        if isinstance(traffic, dict):
            return str(traffic.get("type") or "traffic filter")
    return str(value or "")


def _wifi_network_label(wlan: Dict[str, Any]) -> str:
    network = wlan.get("network")
    if isinstance(network, dict):
        return str(network.get("name") or network.get("id") or network.get("type") or "")
    return _first(wlan, ("networkId", "networkName", "vlanId"))


def _wifi_security_label(wlan: Dict[str, Any]) -> str:
    security = wlan.get("securityConfiguration")
    if isinstance(security, dict):
        return str(security.get("type") or security.get("authenticationType") or "")
    return _first(wlan, ("securityProtocol", "security", "authMode"))


def _wifi_band_label(wlan: Dict[str, Any]) -> str:
    bands = wlan.get("broadcastingFrequenciesGHz")
    if isinstance(bands, list):
        return ", ".join(str(band) for band in bands)
    return _first(wlan, ("band", "apGroupIds"))


def _auth_guidance(sm: Dict[str, Any], net: Dict[str, Any]) -> List[str]:
    guidance: List[str] = []
    for error in list(sm.get("errors") or []) + list(net.get("errors") or []):
        if not isinstance(error, dict):
            continue
        label = str(error.get("label") or "")
        if error.get("status") in {401, 403} and label == "network_sites" and net.get("connectionType") == "remote":
            guidance.append(
                "Remote connector returned authorization failure. Use a cloud/account API key with console access, or switch this profile to local Network Integration collection with UNIFI_NETWORK_BASE_URL."
            )
        elif error.get("status") in {401, 403} and label == "network_sites":
            guidance.append("Local Network Integration API returned authorization failure. Confirm the key was created in this UniFi Network application and has read access.")
        elif error.get("status") in {401, 403} and label == "site_manager_sites":
            guidance.append("Site Manager returned authorization failure. Use a Site Manager/API key from the UniFi account API area, not a local Network Integration key.")
        if error.get("status") is None and label == "network_sites":
            guidance.append("Local UniFi console could not be reached. Verify VPN/LAN access to UNIFI_NETWORK_BASE_URL or use a cloud/account API key with remote connector access.")
    return sorted(set(guidance))


def build_report(source_dir: str, output_dir: str) -> Dict[str, str]:
    source = Path(source_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    summary = _load_json(source / "collection_summary.json", {})
    sm = summary.get("siteManager") if isinstance(summary.get("siteManager"), dict) else {}
    net = summary.get("networkApplication") if isinstance(summary.get("networkApplication"), dict) else {}
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}

    sm_sites = _items(_load_json(source / str((sm.get("files") or {}).get("sites", "")), [])) if sm.get("files") else []
    sm_devices = _items(_load_json(source / str((sm.get("files") or {}).get("devices", "")), [])) if sm.get("files") else []
    site_summaries = _items(_load_json(source / "network_site_summaries.json", []))

    all_devices: List[Dict[str, Any]] = []
    all_clients: List[Dict[str, Any]] = []
    for site in site_summaries:
        all_devices.extend(_read_site_file(source, site, "devices"))
        all_clients.extend(_read_site_file(source, site, "clients"))
    if not all_devices:
        all_devices = sm_devices

    role_counts = _count_by(all_devices, _device_role)
    status_counts = _count_by(all_devices, _status)
    cards = [
        ("Sites", len(site_summaries) or len(sm_sites)),
        ("Devices", len(all_devices)),
        ("Clients", len(all_clients)),
        ("Switches", role_counts.get("Switch", 0)),
        ("APs", role_counts.get("Access Point", 0)),
        ("Gateways", role_counts.get("Gateway", 0)),
    ]

    sections: List[str] = []
    sections.append("<section><h2>Executive Summary</h2>")
    sections.append(_summary_cards(cards))
    guidance = [
        "This first UniFi report is intentionally coverage-oriented: it proves API access, preserves raw JSON backups, and surfaces what the controller exposes for inventory, clients, networks, WiFi, and security policy.",
        "If local Network Application credentials are available, this report should become the primary disaster-recovery and migration source because it captures site-scoped configuration instead of only cloud-level status.",
        "Endpoint failures are listed explicitly so we can refine the collector against the exact UniFi Network version without losing the data that was available.",
    ]
    sections.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in guidance) + "</ul></section>")

    sections.append("<section><h2>Collection Coverage</h2>")
    rows = [
        ["Requested mode", metadata.get("requestedMode", "")],
        ["Effective mode", metadata.get("effectiveMode", "")],
        ["Collected at", metadata.get("collectedAt", "")],
        ["Site Manager", "enabled" if sm.get("enabled") else f"not used: {sm.get('reason', '')}"],
        ["Network Application", "enabled" if net.get("enabled") else f"not used: {net.get('reason', '')}"],
    ]
    sections.append(_table(["Item", "Value"], rows))
    errors = list(sm.get("errors") or []) + list(net.get("errors") or [])
    error_rows = [[e.get("label", ""), e.get("status", ""), e.get("path", ""), e.get("error", "")[:180]] for e in errors]
    sections.append("<h3>Endpoint Gaps / Errors</h3>")
    sections.append(_table(["Endpoint", "Status", "Path", "Error"], error_rows, "No endpoint errors captured."))
    auth_guidance = _auth_guidance(sm, net)
    if auth_guidance:
        sections.append("<h3>Credential / Access Fix</h3>")
        sections.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in auth_guidance) + "</ul>")
    sections.append("</section>")

    sections.append("<section><h2>Device Inventory</h2>")
    role_rows = [[k, v] for k, v in role_counts.items()]
    status_rows = [[k, v] for k, v in status_counts.items()]
    sections.append("<div class='two-col'><div><h3>By Role</h3>" + _table(["Role", "Count"], role_rows) + "</div>")
    sections.append("<div><h3>By Status</h3>" + _table(["Status", "Count"], status_rows) + "</div></div>")
    device_rows = []
    for dev in all_devices[:300]:
        uidb = dev.get("uidb") if isinstance(dev.get("uidb"), dict) else {}
        device_rows.append([
            _first(dev, ("name", "displayName", "hostname"), _nested(dev, ("meta", "name"), "")),
            _device_role(dev),
            _first(dev, ("model", "modelName"), _first(uidb, ("model", "name"), "")),
            _status(dev),
            _first(dev, ("ipAddress", "ip", "lastIp"), ""),
            _first(dev, ("macAddress", "mac", "id"), ""),
            _first(dev, ("version", "firmwareVersion"), ""),
        ])
    sections.append(_table(["Name", "Role", "Model", "Status", "IP", "MAC / ID", "Firmware"], device_rows))
    sections.append("</section>")

    sections.append("<section><h2>Sites, Networks, VLANs, and DHCP</h2>")
    for site in site_summaries:
        sections.append(f"<h3>{html.escape(str(site.get('name') or site.get('id') or 'Site'))}</h3>")
        networks = _read_site_file(source, site, "networks")
        rows = []
        for netw in networks:
            rows.append([
                _first(netw, ("name", "displayName")),
                _first(netw, ("vlanId", "vlan", "vlan_id")),
                _first(netw, ("enabled",)),
                _first(netw, ("default",)),
                _first(netw, ("management",)),
                _first(netw, ("zoneId",)),
            ])
        sections.append(_table(["Network", "VLAN", "Enabled", "Default", "Management", "Zone ID"], rows, "No network/VLAN endpoint data captured for this site."))
    if not site_summaries:
        sections.append("<p class='muted'>No local Network Application site detail captured yet.</p>")
    sections.append("</section>")

    sections.append("<section><h2>WiFi and Client Visibility</h2>")
    for site in site_summaries:
        wifi = _read_site_file(source, site, "wifi")
        rows = []
        for wlan in wifi:
            rows.append([
                _first(wlan, ("name", "ssid")),
                _first(wlan, ("enabled", "isEnabled")),
                _wifi_security_label(wlan),
                _wifi_network_label(wlan),
                _wifi_band_label(wlan),
            ])
        sections.append(f"<h3>{html.escape(str(site.get('name') or 'Site'))}</h3>")
        sections.append(_table(["SSID", "Enabled", "Security", "Network / VLAN", "Band / AP Groups"], rows, "No WiFi endpoint data captured for this site."))
    client_rows = []
    for client in all_clients[:300]:
        client_rows.append([
            _first(client, ("name", "hostname", "displayName")),
            _first(client, ("type", "connectionType")),
            _first(client, ("ipAddress", "ip")),
            _first(client, ("macAddress", "mac", "id")),
            _first(client, ("networkName", "vlanId", "networkId")),
            _first(client, ("connectedAt", "lastSeen")),
        ])
    sections.append("<h3>Connected Clients</h3>")
    sections.append(_table(["Name", "Type", "IP", "MAC / ID", "Network / VLAN", "Seen"], client_rows, "No client detail captured."))
    sections.append("</section>")

    sections.append("<section><h2>Firewall and Policy Backup</h2>")
    for site in site_summaries:
        sections.append(f"<h3>{html.escape(str(site.get('name') or 'Site'))}</h3>")
        zones = _read_site_file(source, site, "firewall_zones")
        zone_names = {str(zone.get("id")): str(zone.get("name") or zone.get("id")) for zone in zones if zone.get("id")}
        for key, label in (
            ("firewall_zones", "Firewall Zones"),
            ("firewall_policies", "Firewall Policies"),
            ("acl_rules", "ACL Rules"),
            ("traffic_lists", "Traffic Lists"),
            ("dns_policies", "DNS Policies"),
        ):
            data = _read_site_file(source, site, key)
            if key == "firewall_policies":
                rows = [
                    [
                        _first(item, ("index",)),
                        _first(item, ("name", "description", "id")),
                        _first(item, ("enabled",)),
                        _action_label(item),
                        _zone_label(item.get("source"), zone_names),
                        _zone_label(item.get("destination"), zone_names),
                        _first(item, ("loggingEnabled",)),
                    ]
                    for item in data[:120]
                ]
                headers = ["Order", "Name", "Enabled", "Action", "Source", "Destination", "Logging"]
            else:
                rows = [[_first(item, ("name", "description", "id")), _first(item, ("enabled", "action", "type")), _first(item, ("id", "_id"))] for item in data[:100]]
                headers = ["Name", "State / Action", "ID"]
            sections.append(f"<h4>{html.escape(label)}</h4>")
            sections.append(_table(headers, rows, f"No {label.lower()} endpoint data captured."))
    sections.append("</section>")

    sections.append("<section><h2>Raw Backup Files</h2>")
    files = sorted(str(p.relative_to(source)) for p in source.rglob("*.json"))
    sections.append(_table(["JSON backup"], [[f] for f in files], "No JSON backup files found."))
    sections.append("</section>")

    html_doc = _html_shell("TM UniFi Baseline", "\n".join(sections), metadata)
    html_path = output / "report.html"
    pdf_path = output / "report.pdf"
    html_path.write_text(html_doc, encoding="utf-8")
    rendered = _render_pdf(html_path, pdf_path)
    return {"html": str(html_path), "pdf": str(pdf_path) if rendered else ""}


def _html_shell(title: str, body: str, metadata: Dict[str, Any]) -> str:
    release = datetime.now().strftime("%Y_%m_%d")
    collected = metadata.get("collectedAt") or "not captured"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    @page {{
      margin: 18mm 12mm 16mm;
      @top-left {{ content: "TM UniFi Baseline"; color: #475569; font-size: 8px; font-weight: 700; }}
      @top-right {{ content: "Release {release}"; color: #64748b; font-size: 8px; }}
      @bottom-center {{ content: "Page " counter(page) " of " counter(pages); color: #64748b; font-size: 8px; }}
    }}
    body {{ margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #0f172a; background: #f8fafc; font-size: 11px; }}
    .cover {{ min-height: 240mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }}
    h1 {{ font-size: 42px; margin: 0 0 12px; }}
    h2 {{ font-size: 19px; margin: 28px 0 10px; border-bottom: 1px solid #cbd5e1; padding-bottom: 5px; }}
    h3 {{ font-size: 14px; margin: 18px 0 8px; }}
    h4 {{ font-size: 11px; margin: 12px 0 6px; color: #334155; }}
    table {{ width: 100%; border-collapse: collapse; margin: 7px 0 12px; table-layout: fixed; }}
    th, td {{ border: 1px solid #dbe3ea; padding: 4px 5px; vertical-align: top; word-break: break-word; }}
    th {{ background: #e2e8f0; text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .03em; }}
    td {{ background: #fff; }}
    .muted {{ color: #64748b; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 14px 0; }}
    .card {{ border: 1px solid #cbd5e1; background: #fff; padding: 10px; }}
    .metric {{ font-size: 24px; font-weight: 750; }}
    .label {{ color: #64748b; font-size: 9px; text-transform: uppercase; letter-spacing: .08em; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    section {{ page-break-inside: auto; }}
  </style>
</head>
<body>
  <section class="cover">
    <p class="muted">TM UniFi Baseline</p>
    <h1>UniFi Network Report</h1>
    <p>Inventory, configuration backup coverage, client visibility, and migration planning inputs.</p>
    <p class="muted">Collected: {html.escape(str(collected))}</p>
  </section>
  {body}
</body>
</html>"""


def _render_pdf(html_path: Path, pdf_path: Path) -> bool:
    try:
        from weasyprint import HTML

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True
    except Exception:
        tool = shutil.which("wkhtmltopdf")
        if not tool:
            return False
        subprocess.run([tool, str(html_path), str(pdf_path)], check=True)
        return True


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate UniFi report from collected JSON.")
    parser.add_argument("--source-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    parser.add_argument("--output-dir", default=str(ROOT / "unifi" / "reports" / "latest"))
    parser.add_argument("--pdf-only", action="store_true")
    args = parser.parse_args(argv)
    paths = build_report(args.source_dir, args.output_dir)
    if args.pdf_only and paths.get("pdf"):
        try:
            Path(paths["html"]).unlink()
        except FileNotFoundError:
            pass
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
