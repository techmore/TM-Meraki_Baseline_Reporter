#!/usr/bin/env python3
import argparse
import html
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
SITE_ENDPOINT_ORDER = [
    "devices",
    "clients",
    "networks",
    "wifi",
    "wans",
    "firewall_zones",
    "firewall_policies",
    "acl_rules",
    "traffic_lists",
    "dns_policies",
    "radius",
    "hotspot_vouchers",
    "vpn_servers",
    "vpn_tunnels",
    "telemetry_probe",
]
SITE_ENDPOINT_LABELS = {
    "acl_rules": "ACL rules",
    "clients": "Clients",
    "devices": "Devices",
    "dns_policies": "DNS policies",
    "firewall_policies": "Firewall policies",
    "firewall_zones": "Firewall zones",
    "hotspot_vouchers": "Hotspot vouchers",
    "networks": "Networks / VLANs",
    "radius": "RADIUS profiles",
    "telemetry_probe": "Telemetry probes",
    "traffic_lists": "Traffic lists",
    "vpn_servers": "VPN servers",
    "vpn_tunnels": "VPN tunnels",
    "wans": "WANs",
    "wifi": "WiFi broadcasts",
}


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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _yes_no(value: Any) -> str:
    return "yes" if _as_bool(value) else "no"


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


def _device_name(device: Dict[str, Any]) -> str:
    return _first(device, ("name", "displayName", "hostname", "id"), _nested(device, ("meta", "name"), "Unknown device"))


def _device_model(device: Dict[str, Any]) -> str:
    uidb = device.get("uidb") if isinstance(device.get("uidb"), dict) else {}
    return _first(device, ("model", "modelName"), _first(uidb, ("model", "name"), "Unknown model"))


def _status(device: Dict[str, Any]) -> str:
    return _first(device, ("state", "status", "connectionState", "adoptionState"), "unknown")


def _is_online(device: Dict[str, Any]) -> bool:
    return _status(device).strip().lower() in {"online", "connected", "active", "up"}


def _count_by(items: Iterable[Dict[str, Any]], fn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = fn(item) or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _fmt_counts(counts: Dict[str, int]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in counts.items()) if counts else "none"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _pct(part: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{round((part / total) * 100)}%"


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days_between(start: datetime | None, end: datetime) -> int | None:
    if not start:
        return None
    return max(0, int((end - start).total_seconds() // 86400))


def _model_rows(devices: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    counts: Dict[tuple[str, str], int] = {}
    for device in devices:
        key = (_device_model(device), _device_role(device))
        counts[key] = counts.get(key, 0) + 1
    return [[model, role, count] for (model, role), count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))]


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if value not in (None, ""):
        return [str(value)]
    return []


def _join_list(value: Any) -> str:
    return ", ".join(_string_list(value))


def _interface_summary_rows(devices: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    counts: Dict[str, int] = {}
    for device in devices:
        for interface in set(_string_list(device.get("interfaces"))):
            counts[interface] = counts.get(interface, 0) + 1
    return [[name, count] for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _interface_device_rows(devices: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for device in devices:
        interfaces = _join_list(device.get("interfaces"))
        features = _join_list(device.get("features"))
        detail = "capability flag only" if interfaces else "not advertised"
        rows.append([_device_name(device), _device_model(device), features, interfaces, detail])
    return rows


def _probe_status_label(probe: Dict[str, Any]) -> str:
    if probe.get("available"):
        return "available"
    status = probe.get("status")
    if status:
        return f"HTTP {status}"
    return "not probed"


def _probe_status_summary(probes: Iterable[Dict[str, Any]], terms: Iterable[str], fallback: str) -> str:
    wanted = [term.lower() for term in terms]
    relevant = [
        probe
        for probe in probes
        if any(term in str(probe.get("label") or "").lower() or term in str(probe.get("purpose") or "").lower() for term in wanted)
    ]
    if not relevant:
        return fallback
    if any(probe.get("available") for probe in relevant):
        return "captured by API probe"
    statuses = sorted({_probe_status_label(probe) for probe in relevant})
    return f"not exposed by probed endpoints ({', '.join(statuses)})"


def _probe_rows(probes: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for probe in probes:
        rows.append(
            [
                probe.get("label", ""),
                _probe_status_label(probe),
                _yes_no(probe.get("available")),
                probe.get("itemCount", 0),
                probe.get("purpose") or probe.get("note") or "",
            ]
        )
    return rows


def _site_endpoint_key(label: str, site_name: str) -> str:
    prefix = f"{site_name}:"
    if label.startswith(prefix):
        return label[len(prefix) :]
    if ":" in label:
        return label.split(":", 1)[1]
    return label


def _endpoint_issue_map(site_name: str, records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    mapped: Dict[str, Dict[str, Any]] = {}
    for record in records:
        label = str(record.get("label") or "")
        if ":" in label and not label.startswith(f"{site_name}:"):
            continue
        key = _site_endpoint_key(label, site_name)
        if key:
            mapped[key] = record
    return mapped


def _site_file_keys(files: Dict[str, Any]) -> List[str]:
    known = [key for key in SITE_ENDPOINT_ORDER if key in files]
    extra = sorted(key for key in files if key not in SITE_ENDPOINT_ORDER)
    return known + extra


def _backup_count_label(key: str, counts: Dict[str, Any]) -> str:
    if key == "telemetry_probe":
        total = counts.get("telemetry_probe_total")
        available = counts.get("telemetry_probe_available")
        if total is not None or available is not None:
            return f"{available or 0} / {total or 0} available"
    return str(counts.get(key, ""))


def _backup_status_label(key: str, count_label: str, error: Dict[str, Any] | None, unsupported: Dict[str, Any] | None) -> str:
    if unsupported:
        return f"not exposed (HTTP {unsupported.get('status')})" if unsupported.get("status") else "not exposed"
    if error:
        return f"error (HTTP {error.get('status')})" if error.get("status") else "error"
    if key == "telemetry_probe":
        return "probed"
    try:
        count = int(count_label)
    except (TypeError, ValueError):
        return "captured" if count_label else "unknown"
    return "captured" if count > 0 else "captured empty"


def _backup_completeness_rows(site: Dict[str, Any], errors: Iterable[Dict[str, Any]], unsupported: Iterable[Dict[str, Any]]) -> List[List[Any]]:
    files = site.get("files") if isinstance(site.get("files"), dict) else {}
    counts = site.get("counts") if isinstance(site.get("counts"), dict) else {}
    site_name = str(site.get("name") or site.get("id") or "Site")
    error_map = _endpoint_issue_map(site_name, errors)
    unsupported_map = _endpoint_issue_map(site_name, unsupported)
    rows: List[List[Any]] = []
    for key in _site_file_keys(files):
        count_label = _backup_count_label(key, counts)
        rows.append(
            [
                SITE_ENDPOINT_LABELS.get(key, key.replace("_", " ").title()),
                count_label,
                _backup_status_label(key, count_label, error_map.get(key), unsupported_map.get(key)),
                files.get(key, ""),
            ]
        )
    return rows


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


def _health_cards(cards: List[tuple[str, str, str, str]]) -> str:
    return "<div class='health-grid'>" + "".join(
        (
            f"<div class='health-card health-card--{html.escape(status)}'>"
            f"<div class='health-card-domain'>{html.escape(domain)}</div>"
            f"<div class='health-card-stat'>{html.escape(stat)}</div>"
            f"<div class='health-card-detail'>{html.escape(detail)}</div>"
            "</div>"
        )
        for status, domain, stat, detail in cards
    ) + "</div>"


def _html_list(items: List[str], *, ordered: bool = False) -> str:
    if not items:
        return "<p class='muted'>No findings generated.</p>"
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + f"</{tag}>"


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


def _access_label(client: Dict[str, Any]) -> str:
    access = client.get("access")
    if isinstance(access, dict):
        return str(access.get("type") or "")
    return str(access or "")


def _build_device_name_map(devices: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for device in devices:
        label = f"{_device_name(device)} ({_device_model(device)})"
        for key in ("id", "macAddress", "mac"):
            value = device.get(key)
            if value:
                names[str(value)] = label
    return names


def _client_uplink_label(client: Dict[str, Any], device_names: Dict[str, str]) -> str:
    uplink = _first(client, ("uplinkDeviceId", "uplinkDeviceMac", "uplinkDeviceName"))
    return device_names.get(uplink, uplink)


def _surface_state(surface: Dict[str, Any]) -> str:
    if surface.get("enabled"):
        return "enabled"
    return f"not used: {surface.get('reason') or 'not configured'}"


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


def _site_health_rows(site_summaries: List[Dict[str, Any]], all_devices: List[Dict[str, Any]]) -> List[List[Any]]:
    if not site_summaries:
        online = sum(1 for device in all_devices if _is_online(device))
        total = len(all_devices)
        return [["Cloud / account", total, f"{online} / {total} ({_pct(online, total)})", total - online, _fmt_counts(_count_by(all_devices, _device_role))]]

    rows: List[List[Any]] = []
    for site in site_summaries:
        counts = site.get("counts") if isinstance(site.get("counts"), dict) else {}
        site_devices = int(counts.get("devices") or 0)
        rows.append(
            [
                site.get("name") or site.get("id") or "Site",
                site_devices,
                str(counts.get("clients") or 0),
                str(counts.get("networks") or 0),
                str(counts.get("wifi") or 0),
                str(counts.get("firewall_policies") or 0),
            ]
        )
    return rows


def _infrastructure_rows(role_counts: Dict[str, int]) -> List[List[Any]]:
    return [
        [
            "WAN / Edge",
            "UniFi Gateway",
            role_counts.get("Gateway", 0),
            "Internet gateway, routing, firewall policy enforcement, VPN termination, and network services where enabled.",
        ],
        [
            "Distribution / Access",
            "UniFi Switch",
            role_counts.get("Switch", 0),
            "Wired LAN switching, VLAN attachment, uplinks, and PoE edge connectivity. Port-level telemetry depends on API availability.",
        ],
        [
            "Wireless",
            "UniFi Access Point",
            role_counts.get("Access Point", 0),
            "WiFi client access, SSID broadcast, roaming behavior, and RF capacity. Radio/channel telemetry depends on API availability.",
        ],
    ]


def _telemetry_gap_summary(telemetry_probes: List[Dict[str, Any]]) -> str:
    if not telemetry_probes:
        return "No detailed port/radio telemetry probes were captured."
    available = sum(1 for probe in telemetry_probes if probe.get("available"))
    total = len(telemetry_probes)
    if available:
        return f"{available} of {total} telemetry probe endpoint(s) returned data."
    statuses = sorted({_probe_status_label(probe) for probe in telemetry_probes})
    return f"0 of {total} telemetry probe endpoint(s) returned data; observed statuses: {', '.join(statuses)}."


def _wifi_security_weak(wifi: Iterable[Dict[str, Any]]) -> List[str]:
    weak: List[str] = []
    for wlan in wifi:
        security = _wifi_security_label(wlan).upper()
        if "OPEN" in security or "NONE" in security:
            weak.append(f"{_first(wlan, ('name', 'ssid'), 'Unnamed SSID')} uses open/no-auth wireless security.")
        elif "WPA2_PERSONAL" in security or security in {"WPA2", "PSK"}:
            weak.append(f"{_first(wlan, ('name', 'ssid'), 'Unnamed SSID')} uses WPA2 Personal; consider WPA3, private pre-shared keys, or 802.1X where appropriate.")
    return weak


def _legacy_ap_models(devices: Iterable[Dict[str, Any]]) -> List[str]:
    legacy: List[str] = []
    for device in devices:
        if _device_role(device) != "Access Point":
            continue
        model = _device_model(device)
        model_l = model.lower()
        if any(token in model_l for token in ("ac ", "ac-", "ac pro", "iw hd", "nano", "hd")) and not any(token in model_l for token in ("u6", "u7")):
            legacy.append(f"{_device_name(device)} ({model})")
    return legacy


def _client_age_buckets(clients: Iterable[Dict[str, Any]], now: datetime) -> Dict[str, int]:
    buckets = {"0-7 days": 0, "8-30 days": 0, "31+ days": 0, "unknown": 0}
    for client in clients:
        seen = _parse_datetime(_first(client, ("connectedAt", "lastSeen")))
        days = _days_between(seen, now)
        if days is None:
            buckets["unknown"] += 1
        elif days <= 7:
            buckets["0-7 days"] += 1
        elif days <= 30:
            buckets["8-30 days"] += 1
        else:
            buckets["31+ days"] += 1
    return buckets


def _top_risks(
    *,
    all_devices: List[Dict[str, Any]],
    all_clients: List[Dict[str, Any]],
    all_wifi: List[Dict[str, Any]],
    all_firewall_policies: List[Dict[str, Any]],
    all_dns_policies: List[Dict[str, Any]],
    telemetry_probes: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
) -> List[str]:
    risks: List[str] = []
    offline = [_device_name(device) for device in all_devices if not _is_online(device)]
    if offline:
        verb = "reports" if len(offline) == 1 else "report"
        risks.append(f"Device availability requires attention - {_plural(len(offline), 'device')} {verb} offline or inactive: {', '.join(offline[:6])}.")

    if telemetry_probes and not any(probe.get("available") for probe in telemetry_probes):
        risks.append("Port and radio diagnostics are low-confidence - this controller/API path did not expose switch-port or AP-radio telemetry, so PoE draw, RF interference, channel utilization, and port speed cannot be validated from this backup alone.")

    risks.extend(_wifi_security_weak(all_wifi)[:3])

    if all_firewall_policies:
        logging_disabled = sum(1 for policy in all_firewall_policies if not _as_bool(policy.get("loggingEnabled")))
        if logging_disabled:
            risks.append(f"Firewall visibility may be limited - {_plural(logging_disabled, 'captured firewall policy', 'captured firewall policies')} have logging disabled.")
    else:
        risks.append("No firewall policies were captured; do not treat this run as a complete security backup until policy endpoint access is validated.")

    if not all_dns_policies:
        risks.append("No DNS policies were captured; confirm whether DNS filtering is intentionally unused or unavailable from this API surface.")

    if errors:
        risks.append(f"Collection has {_plural(len(errors), 'hard endpoint error')} that should be resolved before using this as a final documentation package.")

    if not all_clients:
        risks.append("Client visibility is absent, limiting capacity planning and migration sizing.")
    return risks or ["No high-priority risks were generated from the captured UniFi data."]


def _recommended_priorities(
    *,
    all_devices: List[Dict[str, Any]],
    all_wifi: List[Dict[str, Any]],
    telemetry_probes: List[Dict[str, Any]],
    all_firewall_policies: List[Dict[str, Any]],
    all_dns_policies: List[Dict[str, Any]],
) -> List[str]:
    priorities: List[str] = []
    if any(not _is_online(device) for device in all_devices):
        priorities.append("Immediate (0-2 weeks): Validate offline UniFi devices against physical inventory, power, uplinks, and controller adoption state.")
    if telemetry_probes and not any(probe.get("available") for probe in telemetry_probes):
        priorities.append("Immediate (0-2 weeks): Decide whether deeper diagnostics require Site Manager metrics, UniFi system log/SIEM export, SSH/local controller export, or manual screenshots because the Integration API did not expose port/radio telemetry.")
    if _wifi_security_weak(all_wifi):
        priorities.append("Short-term (2-6 weeks): Review SSID security and migrate appropriate production WLANs toward WPA3, private PSK, or 802.1X instead of shared WPA2 Personal.")
    if all_firewall_policies and any(not _as_bool(policy.get("loggingEnabled")) for policy in all_firewall_policies):
        priorities.append("Short-term (2-6 weeks): Enable logging on security-relevant block/allow policies where event volume is acceptable.")
    if not all_dns_policies:
        priorities.append("Medium-term (6-12 weeks): Confirm DNS/security filtering requirements and document whether UniFi DNS policies, upstream filtering, or a separate security stack owns that control.")
    priorities.append("Long-term (3-6 months): Build a refresh plan from active devices only, separating replacement candidates from offline/retired inventory.")
    return priorities


def _data_confidence_rows(
    *,
    all_devices: List[Dict[str, Any]],
    all_clients: List[Dict[str, Any]],
    network_count: int,
    firewall_policy_count: int,
    telemetry_probes: List[Dict[str, Any]],
    all_wans: List[Dict[str, Any]],
) -> List[List[Any]]:
    telemetry_available = sum(1 for probe in telemetry_probes if probe.get("available"))
    return [
        ["Inventory and device status", "High" if all_devices else "Low", f"{_plural(len(all_devices), 'device record')} captured with controller state."],
        ["Client attachment detail", "High" if all_clients else "Low", f"{_plural(len(all_clients), 'client record')} captured with uplink mapping where present."],
        ["VLAN/network definitions", "Medium" if network_count else "Low", f"{_plural(network_count, 'network/VLAN definition')} captured; subnet/DHCP detail depends on API fields exposed by this controller."],
        ["Firewall policy backup", "High" if firewall_policy_count else "Low", f"{_plural(firewall_policy_count, 'policy', 'policies')} captured."],
        ["WAN detail", "Low" if all_wans else "Not captured", f"{_plural(len(all_wans), 'WAN record')} captured; current endpoint only exposed labels in this run."],
        ["Port and radio telemetry", "Low" if telemetry_available == 0 else "Medium", _telemetry_gap_summary(telemetry_probes)],
    ]


def _security_baseline_rows(
    *,
    all_wifi: List[Dict[str, Any]],
    all_firewall_policies: List[Dict[str, Any]],
    all_dns_policies: List[Dict[str, Any]],
    all_radius: List[Dict[str, Any]],
    network_count: int,
) -> List[List[Any]]:
    weak_wifi = _wifi_security_weak(all_wifi)
    logging_enabled = sum(1 for policy in all_firewall_policies if _as_bool(policy.get("loggingEnabled")))
    return [
        ["Network segmentation", "Review" if network_count <= 2 else "Present", f"{_plural(network_count, 'network/VLAN definition')} captured."],
        ["Wireless authentication", "Review" if weak_wifi else ("Present" if all_wifi else "Missing"), "; ".join(weak_wifi[:2]) if weak_wifi else f"{_plural(len(all_wifi), 'SSID')} captured."],
        ["Firewall rules", "Present" if all_firewall_policies else "Missing", f"{_plural(len(all_firewall_policies), 'policy', 'policies')} captured."],
        ["Firewall logging", "Review" if all_firewall_policies and logging_enabled < len(all_firewall_policies) else "Present", f"{logging_enabled} of {len(all_firewall_policies)} policies have logging enabled."],
        ["DNS filtering policy", "Missing" if not all_dns_policies else "Present", f"{_plural(len(all_dns_policies), 'DNS policy', 'DNS policies')} captured."],
        ["RADIUS / identity", "Present" if all_radius else "Not captured", f"{_plural(len(all_radius), 'RADIUS profile')} captured."],
    ]


def _executive_followups(
    *,
    all_devices: List[Dict[str, Any]],
    all_clients: List[Dict[str, Any]],
    site_summaries: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    unsupported: List[Dict[str, Any]],
    role_counts: Dict[str, int],
    client_counts: Dict[str, int],
    firewall_policy_count: int,
    enabled_firewall_policy_count: int,
    network_count: int,
    wifi_count: int,
) -> List[str]:
    followups: List[str] = []
    offline = [_device_name(device) for device in all_devices if not _is_online(device)]
    updatable = [_device_name(device) for device in all_devices if _as_bool(device.get("firmwareUpdatable"))]

    if offline:
        followups.append(f"Validate offline inventory before migration planning: {', '.join(offline[:6])}.")
    else:
        followups.append("All captured UniFi devices report online in the latest backup.")

    if updatable:
        followups.append(f"Review available firmware updates for: {', '.join(updatable[:6])}.")
    else:
        followups.append("No captured UniFi devices are currently flagged as firmware-updatable by the controller.")

    if role_counts.get("Access Point", 0) and all_clients:
        followups.append(f"Wireless client load is visible in this backup ({_fmt_counts(client_counts)}), giving an initial input for AP replacement and capacity planning.")
    elif role_counts.get("Access Point", 0):
        followups.append("AP inventory is captured, but client detail is missing; confirm client endpoint access before using the report for wireless capacity planning.")

    if network_count:
        followups.append(f"Network backup includes {_plural(network_count, 'VLAN/network definition')} and {_plural(wifi_count, 'WiFi broadcast definition')}.")
    else:
        followups.append("No VLAN/network endpoint data was captured; validate Network Application API permissions.")

    if firewall_policy_count:
        followups.append(f"Firewall backup includes {enabled_firewall_policy_count} enabled policies out of {_plural(firewall_policy_count, 'captured policy', 'captured policies')}.")
    else:
        followups.append("No firewall policies were captured; validate security policy endpoint access before treating this as a disaster-recovery backup.")

    if errors:
        followups.append(f"Resolve {_plural(len(errors), 'collection error')} listed in Collection Coverage.")
    if unsupported:
        if len(unsupported) == 1:
            followups.append("1 optional endpoint is not exposed by this controller version; it is documented as a coverage note.")
        else:
            followups.append(f"{len(unsupported)} optional endpoints are not exposed by this controller version; they are documented as coverage notes.")
    if not site_summaries:
        followups.append("Only cloud-level data was captured; use local Network Application credentials for site-scoped configuration backup.")
    return followups


def build_report(source_dir: str, output_dir: str) -> Dict[str, str]:
    source = Path(source_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    summary = _load_json(source / "collection_summary.json", {})
    sm = summary.get("siteManager") if isinstance(summary.get("siteManager"), dict) else {}
    net = summary.get("networkApplication") if isinstance(summary.get("networkApplication"), dict) else {}
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
    network_info = _load_json(source / str((net.get("files") or {}).get("info", "network_info.json")), {})
    if not isinstance(network_info, dict):
        network_info = {}

    sm_sites = _items(_load_json(source / str((sm.get("files") or {}).get("sites", "")), [])) if sm.get("files") else []
    sm_devices = _items(_load_json(source / str((sm.get("files") or {}).get("devices", "")), [])) if sm.get("files") else []
    site_summaries = _items(_load_json(source / "network_site_summaries.json", []))

    all_devices: List[Dict[str, Any]] = []
    all_clients: List[Dict[str, Any]] = []
    telemetry_probes: List[Dict[str, Any]] = []
    for site in site_summaries:
        all_devices.extend(_read_site_file(source, site, "devices"))
        all_clients.extend(_read_site_file(source, site, "clients"))
        telemetry_probes.extend(_read_site_file(source, site, "telemetry_probe"))
    if not all_devices:
        all_devices = sm_devices

    role_counts = _count_by(all_devices, _device_role)
    status_counts = _count_by(all_devices, _status)
    client_counts = _count_by(all_clients, lambda client: _first(client, ("type", "connectionType"), "Unknown"))
    all_site_counts = [site.get("counts") for site in site_summaries if isinstance(site.get("counts"), dict)]
    network_count = sum(int(counts.get("networks") or 0) for counts in all_site_counts)
    wifi_count = sum(int(counts.get("wifi") or 0) for counts in all_site_counts)
    firewall_zone_count = sum(int(counts.get("firewall_zones") or 0) for counts in all_site_counts)
    firewall_policy_count = sum(int(counts.get("firewall_policies") or 0) for counts in all_site_counts)
    site_payloads: Dict[str, List[Dict[str, Any]]] = {
        key: []
        for key in (
            "networks",
            "wifi",
            "wans",
            "firewall_zones",
            "firewall_policies",
            "acl_rules",
            "traffic_lists",
            "dns_policies",
            "radius",
        )
    }
    for site in site_summaries:
        for key in site_payloads:
            site_payloads[key].extend(_read_site_file(source, site, key))
    enabled_firewall_policy_count = 0
    for site in site_summaries:
        enabled_firewall_policy_count += sum(1 for policy in _read_site_file(source, site, "firewall_policies") if _as_bool(policy.get("enabled")))
    errors = list(sm.get("errors") or []) + list(net.get("errors") or [])
    unsupported = list(sm.get("unsupportedEndpoints") or []) + list(net.get("unsupportedEndpoints") or [])
    device_names = _build_device_name_map(all_devices)
    collected_at = _parse_datetime(metadata.get("collectedAt")) or datetime.now(timezone.utc)
    online_devices = sum(1 for device in all_devices if _is_online(device))
    offline_devices = len(all_devices) - online_devices
    updatable_devices = sum(1 for device in all_devices if _as_bool(device.get("firmwareUpdatable")))
    telemetry_available = sum(1 for probe in telemetry_probes if probe.get("available"))
    legacy_aps = _legacy_ap_models(all_devices)
    client_age = _client_age_buckets(all_clients, collected_at)
    cards = [
        ("Sites", len(site_summaries) or len(sm_sites)),
        ("Devices", len(all_devices)),
        ("Clients", len(all_clients)),
        ("Switches", role_counts.get("Switch", 0)),
        ("APs", role_counts.get("Access Point", 0)),
        ("Gateways", role_counts.get("Gateway", 0)),
        ("Networks", network_count),
        ("WiFi", wifi_count),
        ("Firewall Policies", firewall_policy_count),
    ]

    sections: List[str] = []
    sections.append("<section><h2>1. Executive Summary</h2>")
    current_state = (
        f"This UniFi assessment covers {len(site_summaries) or len(sm_sites) or 1} site(s) with "
        f"{_plural(len(all_devices), 'captured UniFi device')} and {_plural(len(all_clients), 'client record')}. "
        f"{online_devices} of {len(all_devices)} devices ({_pct(online_devices, len(all_devices))}) report online. "
        "The report emphasizes actionable configuration and client visibility, while explicitly calling out telemetry gaps where the UniFi API did not expose switch-port or AP-radio metrics."
    )
    sections.append(f"<div class='summary-card'><div class='summary-title'>Current State Assessment</div><div class='summary-body'>{html.escape(current_state)}</div></div>")
    sections.append(_summary_cards(cards))
    top_risks = _top_risks(
        all_devices=all_devices,
        all_clients=all_clients,
        all_wifi=site_payloads["wifi"],
        all_firewall_policies=site_payloads["firewall_policies"],
        all_dns_policies=site_payloads["dns_policies"],
        telemetry_probes=telemetry_probes,
        errors=errors,
    )
    sections.append("<h3>Top Operational Risks</h3>")
    sections.append(_html_list(top_risks))
    sections.append("<h3>Recommended Priorities</h3>")
    sections.append(
        _html_list(
            _recommended_priorities(
                all_devices=all_devices,
                all_wifi=site_payloads["wifi"],
                telemetry_probes=telemetry_probes,
                all_firewall_policies=site_payloads["firewall_policies"],
                all_dns_policies=site_payloads["dns_policies"],
            ),
            ordered=True,
        )
    )
    sections.append("<h3>Infrastructure Inventory</h3>")
    sections.append(_table(["Layer", "Device Type", "Count", "Role in Network"], _infrastructure_rows(role_counts)))
    site_rows = []
    for site in site_summaries:
        counts = site.get("counts") if isinstance(site.get("counts"), dict) else {}
        site_name = str(site.get("name") or site.get("id") or "Site")
        site_coverage_notes = sum(1 for item in unsupported if str(item.get("label") or "").startswith(f"{site_name}:"))
        if not site_coverage_notes and len(site_summaries) == 1:
            site_coverage_notes = len(unsupported)
        site_rows.append(
            [
                site_name,
                counts.get("devices", 0),
                counts.get("clients", 0),
                counts.get("networks", 0),
                counts.get("wifi", 0),
                counts.get("firewall_policies", 0),
                site_coverage_notes,
            ]
        )
    sections.append("<h3>Site Capture Summary</h3>")
    sections.append(_table(["Site", "Devices", "Clients", "Networks", "WiFi", "Firewall Policies", "Coverage Notes"], site_rows, "No local site detail captured."))
    summary_rows = [
        ["Inventory", f"{len(all_devices)} devices captured ({_fmt_counts(role_counts)})."],
        ["Clients", f"{len(all_clients)} clients captured ({_fmt_counts(client_counts)})."],
        [
            "Configuration backup",
            f"{_plural(network_count, 'network/VLAN', 'networks/VLANs')}, {_plural(wifi_count, 'WiFi broadcast')}, {_plural(firewall_zone_count, 'firewall zone')}, and {_plural(firewall_policy_count, 'firewall policy', 'firewall policies')} captured.",
        ],
        ["Collection coverage", f"{_plural(len(errors), 'hard endpoint error')}; {_plural(len(unsupported), 'optional endpoint coverage note')}."],
    ]
    sections.append("<h3>What This Run Captured</h3>")
    sections.append(_table(["Area", "Summary"], summary_rows))
    followups = _executive_followups(
        all_devices=all_devices,
        all_clients=all_clients,
        site_summaries=site_summaries,
        errors=errors,
        unsupported=unsupported,
        role_counts=role_counts,
        client_counts=client_counts,
        firewall_policy_count=firewall_policy_count,
        enabled_firewall_policy_count=enabled_firewall_policy_count,
        network_count=network_count,
        wifi_count=wifi_count,
    )
    sections.append("<h3>Recommended Follow-Up</h3>")
    sections.append(_html_list(followups))
    sections.append("<h3>Data Confidence Snapshot</h3>")
    sections.append(
        _table(
            ["Data Area", "Confidence", "Interpretation"],
            _data_confidence_rows(
                all_devices=all_devices,
                all_clients=all_clients,
                network_count=network_count,
                firewall_policy_count=firewall_policy_count,
                telemetry_probes=telemetry_probes,
                all_wans=site_payloads["wans"],
            ),
        )
    )
    health_cards = [
        ("crit" if offline_devices else "good", "Availability", f"{_pct(online_devices, len(all_devices))} online", f"{online_devices} online / {offline_devices} offline"),
        ("warn" if legacy_aps else "good", "Wireless", f"{role_counts.get('Access Point', 0)} APs", f"{len(legacy_aps)} legacy candidate(s)"),
        ("warn" if telemetry_available == 0 and telemetry_probes else "good", "Port / RF Telemetry", f"{telemetry_available}/{len(telemetry_probes)} probes", "Port/radio detail availability"),
        ("good" if site_payloads["firewall_policies"] else "warn", "Firewall Backup", f"{enabled_firewall_policy_count} enabled", f"{firewall_policy_count} captured policies"),
        ("warn" if _wifi_security_weak(site_payloads["wifi"]) else "good", "WiFi Security", f"{wifi_count} SSID", "Authentication posture"),
        ("info", "Clients", str(len(all_clients)), f"{client_age['31+ days']} stale over 30 days"),
        ("warn" if updatable_devices else "good", "Firmware", f"{updatable_devices} updates", "Controller update flag"),
        ("warn" if not site_payloads["dns_policies"] else "good", "DNS Policy", str(len(site_payloads["dns_policies"])), "No DNS policies captured" if not site_payloads["dns_policies"] else "Captured DNS controls"),
    ]
    sections.append("<h3>Health at a Glance</h3>")
    sections.append(_health_cards(health_cards))
    sections.append("</section>")

    sections.append("<section><h2>Guide. How to Use This Report</h2>")
    sections.append(
        _table(
            ["Reader", "Start Here", "Why"],
            [
                ["Leadership / Finance", "Executive Summary and Recommended Priorities", "Shows the largest risks, follow-up actions, and where current data is strong or weak."],
                ["IT Operations", "Device Inventory, Client Visibility, and Sites / VLANs", "Connects inventory, clients, VLANs, and operational symptoms without relying on unavailable telemetry."],
                ["Security / Compliance", "Security Baseline and Firewall Policy Backup", "Documents firewall, DNS, RADIUS, SSID, and policy evidence captured by the UniFi API."],
                ["Implementation Team", "Configuration Backup Completeness and Raw Backup Files", "Shows which JSON files can support disaster recovery or migration planning."],
            ],
        )
    )
    sections.append("</section>")

    sections.append("<section><h2>2. Collection Coverage</h2>")
    rows = [
        ["Requested mode", metadata.get("requestedMode", "")],
        ["Effective mode", metadata.get("effectiveMode", "")],
        ["Collected at", metadata.get("collectedAt", "")],
        ["Network Application version", network_info.get("applicationVersion", "")],
        ["Site Manager", _surface_state(sm)],
        ["Network Application", _surface_state(net)],
    ]
    sections.append(_table(["Item", "Value"], rows))
    error_rows = [[e.get("label", ""), e.get("status", ""), e.get("path", ""), e.get("error", "")[:180]] for e in errors]
    sections.append("<h3>Endpoint Gaps / Errors</h3>")
    sections.append(_table(["Endpoint", "Status", "Path", "Error"], error_rows, "No endpoint errors captured."))
    unsupported_rows = [[e.get("label", ""), e.get("status", ""), e.get("path", ""), e.get("note", "")] for e in unsupported]
    if unsupported_rows:
        sections.append("<h3>Optional API Coverage Notes</h3>")
        sections.append(_table(["Endpoint", "Status", "Path", "Note"], unsupported_rows))
    auth_guidance = _auth_guidance(sm, net)
    if auth_guidance:
        sections.append("<h3>Credential / Access Fix</h3>")
        sections.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in auth_guidance) + "</ul>")
    sections.append("</section>")

    sections.append("<section><h2>3. Network Overview</h2>")
    sections.append("<p>This section gives the operations view of captured sites before the lower-level backup tables. Client and configuration counts are useful for migration planning even when detailed switch-port and AP-radio telemetry is unavailable.</p>")
    sections.append(_table(["Site", "Devices", "Clients", "Networks", "WiFi", "Firewall Policies"], _site_health_rows(site_summaries, all_devices), "No site summary captured."))
    sections.append("</section>")

    sections.append("<section><h2>4. Configuration Backup Completeness</h2>")
    if site_summaries:
        for site in site_summaries:
            sections.append(f"<h3>{html.escape(str(site.get('name') or site.get('id') or 'Site'))}</h3>")
            sections.append(
                _table(
                    ["Area", "Items", "Status", "Backup JSON"],
                    _backup_completeness_rows(site, errors, unsupported),
                    "No site-scoped backup files were captured.",
                )
            )
    else:
        sections.append("<p class='muted'>No local Network Application site backup detail captured.</p>")
    sections.append("</section>")

    sections.append("<section><h2>5. Device Health &amp; Inventory</h2>")
    role_rows = [[k, v] for k, v in role_counts.items()]
    status_rows = [[k, v] for k, v in status_counts.items()]
    sections.append("<div class='two-col'><div><h3>By Role</h3>" + _table(["Role", "Count"], role_rows) + "</div>")
    sections.append("<div><h3>By Status</h3>" + _table(["Status", "Count"], status_rows) + "</div></div>")
    sections.append("<h3>By Model</h3>")
    sections.append(_table(["Model", "Role", "Count"], _model_rows(all_devices), "No device model data captured."))
    sections.append("<h3>Interface Telemetry Coverage</h3>")
    if telemetry_probes:
        sections.append("<p class='muted'>UniFi Network reports interface capability flags in this backup. API probe results below document whether detailed per-port and per-radio endpoints were exposed by this controller.</p>")
    else:
        sections.append("<p class='muted'>UniFi Network reports interface capability flags in this backup. Per-port and per-radio utilization metrics are not present in the captured Network Integration payloads.</p>")
    sections.append("<div class='two-col'><div><h4>Advertised Interfaces</h4>" + _table(["Interface", "Devices"], _interface_summary_rows(all_devices), "No interface capability flags captured.") + "</div>")
    telemetry_status_rows = [
        ["Port detail", _probe_status_summary(telemetry_probes, ("port", "ports"), "not present in backup")],
        ["Radio detail", _probe_status_summary(telemetry_probes, ("radio", "radios", "rf"), "not present in backup")],
        ["Client uplink mapping", "captured" if all_clients else "not present in backup"],
    ]
    sections.append("<div><h4>Telemetry Status</h4>" + _table(["Metric", "Status"], telemetry_status_rows) + "</div></div>")
    sections.append(_table(["Device", "Model", "Features", "Interfaces", "Detail"], _interface_device_rows(all_devices), "No device interface coverage captured."))
    if telemetry_probes:
        sections.append("<h4>API Telemetry Probe Results</h4>")
        sections.append(_table(["Probe", "Status", "Available", "Items", "Purpose"], _probe_rows(telemetry_probes), "No telemetry probes captured."))
    device_rows = []
    for dev in all_devices[:300]:
        device_rows.append([
            _device_name(dev),
            _device_role(dev),
            _device_model(dev),
            _status(dev),
            _yes_no(dev.get("firmwareUpdatable")),
            _first(dev, ("ipAddress", "ip", "lastIp"), ""),
            _first(dev, ("macAddress", "mac", "id"), ""),
            _first(dev, ("version", "firmwareVersion"), ""),
        ])
    sections.append(_table(["Name", "Role", "Model", "Status", "Update", "IP", "MAC / ID", "Firmware"], device_rows))
    sections.append("</section>")

    sections.append("<section><h2>6. Sites, Networks, VLANs, and DHCP</h2>")
    for site in site_summaries:
        sections.append(f"<h3>{html.escape(str(site.get('name') or site.get('id') or 'Site'))}</h3>")
        networks = _read_site_file(source, site, "networks")
        zones = _read_site_file(source, site, "firewall_zones")
        zone_names = {str(zone.get("id")): str(zone.get("name") or zone.get("id")) for zone in zones if zone.get("id")}
        rows = []
        for netw in networks:
            metadata_payload = netw.get("metadata") if isinstance(netw.get("metadata"), dict) else {}
            rows.append([
                _first(netw, ("name", "displayName")),
                _first(netw, ("vlanId", "vlan", "vlan_id")),
                _yes_no(netw.get("enabled")),
                _yes_no(netw.get("default")),
                _first(netw, ("management",)),
                zone_names.get(str(netw.get("zoneId") or ""), _first(netw, ("zoneId",))),
                _first(metadata_payload, ("origin",)),
            ])
        sections.append(_table(["Network", "VLAN", "Enabled", "Default", "Management", "Zone", "Origin"], rows, "No network/VLAN endpoint data captured for this site."))
    if not site_summaries:
        sections.append("<p class='muted'>No local Network Application site detail captured yet.</p>")
    sections.append("</section>")

    sections.append("<section><h2>7. WiFi and Client Visibility</h2>")
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
    uplink_rows = [[uplink, count] for uplink, count in _count_by(all_clients, lambda client: _client_uplink_label(client, device_names) or "Unknown").items()]
    if uplink_rows:
        sections.append("<h3>Client Load by Uplink</h3>")
        sections.append(_table(["Uplink Device", "Clients"], uplink_rows))
    client_rows = []
    for client in all_clients[:300]:
        client_rows.append([
            _first(client, ("name", "hostname", "displayName")),
            _first(client, ("type", "connectionType")),
            _first(client, ("ipAddress", "ip")),
            _first(client, ("macAddress", "mac", "id")),
            _client_uplink_label(client, device_names),
            _access_label(client),
            _first(client, ("connectedAt", "lastSeen")),
        ])
    sections.append("<h3>Connected Clients</h3>")
    sections.append(_table(["Name", "Type", "IP", "MAC / ID", "Uplink Device", "Access", "Seen"], client_rows, "No client detail captured."))
    sections.append("</section>")

    sections.append("<section><h2>8. Security Baseline</h2>")
    sections.append(
        _table(
            ["Control Area", "Status", "Evidence / Interpretation"],
            _security_baseline_rows(
                all_wifi=site_payloads["wifi"],
                all_firewall_policies=site_payloads["firewall_policies"],
                all_dns_policies=site_payloads["dns_policies"],
                all_radius=site_payloads["radius"],
                network_count=network_count,
            ),
        )
    )
    sections.append("<p class='muted'>Security baseline rows are assessment cues from captured configuration, not a substitute for a full policy review. Missing rows may mean the control is implemented outside UniFi or not exposed by this API path.</p>")
    sections.append("</section>")

    sections.append("<section><h2>9. Firewall and Policy Backup</h2>")
    for site in site_summaries:
        sections.append(f"<h3>{html.escape(str(site.get('name') or 'Site'))}</h3>")
        zones = _read_site_file(source, site, "firewall_zones")
        zone_names = {str(zone.get("id")): str(zone.get("name") or zone.get("id")) for zone in zones if zone.get("id")}
        policies = _read_site_file(source, site, "firewall_policies")
        if policies:
            policy_action_rows = [[action, count] for action, count in _count_by(policies, _action_label).items()]
            policy_enabled_rows = [
                ["Enabled", sum(1 for policy in policies if _as_bool(policy.get("enabled")))],
                ["Disabled", sum(1 for policy in policies if not _as_bool(policy.get("enabled")))],
            ]
            sections.append("<h4>Firewall Policy Summary</h4>")
            sections.append("<div class='two-col'><div>" + _table(["Action", "Count"], policy_action_rows) + "</div>")
            sections.append("<div>" + _table(["State", "Count"], policy_enabled_rows) + "</div></div>")
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

    sections.append("<section><h2>10. Raw Backup Files</h2>")
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
    @font-face {{
      font-family: "Inter";
      font-weight: 300 700;
      src: local("Inter"), local("Inter-Regular");
    }}
    @font-face {{
      font-family: "Playfair Display";
      font-weight: 600 700;
      src: local("Playfair Display"), local("PlayfairDisplay-Bold");
    }}
    @page cover-page {{
      margin: 0;
    }}
    @page {{
      margin: 22mm 12mm 20mm;
      background: var(--olive-100);
      @top-left {{
        content: "TM UNIFI BASELINE";
        color: #575d3d;
        font-family: "Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 8px;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }}
      @top-right {{
        content: "Release {release}";
        color: #78716c;
        font-family: "Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 8px;
      }}
      @bottom-center {{
        content: "Page " counter(page) " of " counter(pages);
        color: #78716c;
        font-family: "Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 8px;
      }}
    }}
    :root {{
      --bg: #eef0e6;
      --ink: #0f172a;
      --muted: #64748b;
      --line: #e2e8f0;
      --olive-50: #f7f8f4;
      --olive-100: #eef0e6;
      --olive-200: #dde1d0;
      --olive-300: #c4c9b0;
      --olive-400: #a7ae8b;
      --olive-500: #8a9269;
      --olive-700: #575d3d;
      --olive-800: #464a34;
      --olive-900: #3b3e2d;
      --olive-950: #1f2117;
      --stone-50: #fafaf9;
      --stone-100: #f5f5f4;
      --stone-200: #e7e5e4;
      --stone-700: #44403c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Inter", system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
      font-size: 11px;
    }}
    .cover {{
      page: cover-page;
      height: 297mm;
      background: linear-gradient(150deg, var(--olive-950) 0%, var(--olive-900) 45%, var(--olive-800) 100%);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      page-break-after: always;
      text-align: center;
    }}
    .cover-inner {{
      width: 100%;
      max-width: 700px;
      padding: 80px 60px 60px;
      min-height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .cover-brand {{
      font-size: 11px;
      letter-spacing: 0.3em;
      text-transform: uppercase;
      color: var(--olive-200);
      opacity: 0.72;
      margin-bottom: 32px;
    }}
    .cover-rule {{
      width: 48px;
      height: 2px;
      background: var(--olive-400);
      margin: 0 auto 36px;
    }}
    .cover-title {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 42px;
      line-height: 1.15;
      color: #fff;
      margin: 0 0 20px;
    }}
    .cover-subtitle {{
      font-size: 18px;
      color: var(--olive-200);
      margin: 0 0 12px;
    }}
    .cover-run-ts {{
      font-size: 11px;
      color: var(--olive-200);
      opacity: 0.72;
    }}
    .cover-bottom-rule {{
      width: 100%;
      height: 1px;
      background: var(--olive-700);
      margin-bottom: 18px;
      opacity: 0.5;
    }}
    .cover-bottom-info {{
      display: flex;
      justify-content: space-between;
      color: var(--olive-300);
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      opacity: 0.7;
    }}
    .toc-page {{
      page-break-after: always;
      min-height: 241mm;
      padding: 44px 64px;
    }}
    .toc-header {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 28px;
      font-weight: 700;
      color: var(--olive-900);
      border-bottom: 2px solid var(--olive-400);
      padding-bottom: 12px;
      margin-bottom: 24px;
    }}
    .toc-list {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .toc-list li {{
      display: flex;
      gap: 12px;
      padding: 7px 0;
      border-bottom: 1px solid var(--line);
      font-size: 12px;
    }}
    .toc-num {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 14px;
      font-weight: 700;
      color: var(--olive-400);
      min-width: 28px;
    }}
    h1 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 26px;
      margin: 0 0 4px;
      color: var(--olive-900);
    }}
    h2 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 22px;
      margin: 24px 0 12px;
      border-bottom: 2px solid var(--olive-300);
      padding-bottom: 8px;
      color: var(--olive-900);
    }}
    h3 {{
      font-family: "Playfair Display", Georgia, "Times New Roman", serif;
      font-size: 15px;
      margin: 18px 0 8px;
      color: var(--olive-800);
    }}
    h4 {{ font-size: 11px; margin: 12px 0 6px; color: #334155; }}
    table {{ width: 100%; border-collapse: collapse; margin: 7px 0 12px; table-layout: fixed; }}
    th, td {{ border: 1px solid #dbe3ea; padding: 4px 5px; vertical-align: top; word-break: break-word; }}
    th {{ background: var(--olive-200); text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .03em; color: var(--olive-900); }}
    td {{ background: #fff; }}
    .muted {{ color: #64748b; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 14px 0; }}
    .card {{ border: 1px solid var(--line); background: var(--stone-50); padding: 12px; border-radius: 10px; }}
    .metric {{ font-family: "Playfair Display", Georgia, "Times New Roman", serif; font-size: 24px; font-weight: 750; color: var(--olive-900); }}
    .label {{ color: #64748b; font-size: 9px; text-transform: uppercase; letter-spacing: .08em; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .summary-card {{
      background: var(--stone-50);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px 24px;
      margin: 16px 0 24px;
      position: relative;
      overflow: hidden;
    }}
    .summary-card::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      height: 4px;
      width: 100%;
      background: linear-gradient(90deg, var(--olive-500), var(--olive-300));
    }}
    .summary-title {{
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 600;
    }}
    .summary-body {{ font-size: 12px; line-height: 1.6; }}
    .health-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 16px 0 20px; }}
    .health-card {{
      border: 1px solid var(--line);
      background: var(--stone-50);
      border-radius: 10px;
      padding: 14px 14px 12px;
      position: relative;
      overflow: hidden;
    }}
    .health-card::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 3px; }}
    .health-card--good::before {{ background: #22c55e; }}
    .health-card--warn::before {{ background: #f59e0b; }}
    .health-card--crit::before {{ background: #ef4444; }}
    .health-card--info::before {{ background: var(--olive-400); }}
    .health-card-domain {{ font-size: 8.5px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); font-weight: 600; }}
    .health-card-stat {{ font-family: "Playfair Display", Georgia, "Times New Roman", serif; font-size: 20px; font-weight: 700; color: var(--ink); line-height: 1.1; }}
    .health-card--crit .health-card-stat {{ color: #dc2626; }}
    .health-card--warn .health-card-stat {{ color: #b45309; }}
    .health-card--good .health-card-stat {{ color: #15803d; }}
    .health-card-detail {{ font-size: 9px; color: var(--muted); margin-top: 3px; }}
    section {{ page-break-inside: auto; }}
  </style>
</head>
<body>
  <section class="cover">
    <div class="cover-inner">
      <div>
        <div class="cover-brand">Techmore</div>
        <div class="cover-rule"></div>
        <h1 class="cover-title">UniFi Network Health &amp; Backup Report</h1>
        <p class="cover-subtitle">TM UniFi Baseline</p>
        <p class="cover-run-ts">Collected: {html.escape(str(collected))}</p>
      </div>
      <div>
        <div class="cover-bottom-rule"></div>
        <div class="cover-bottom-info"><span>Confidential</span><span>Release {release}</span></div>
      </div>
    </div>
  </section>
  <section class="toc-page">
    <div class="toc-header">Table of Contents</div>
    <ol class="toc-list">
      <li><span class="toc-num">1</span><span>Executive Summary</span></li>
      <li><span class="toc-num">Guide</span><span>How to Use This Report</span></li>
      <li><span class="toc-num">2</span><span>Collection Coverage</span></li>
      <li><span class="toc-num">3</span><span>Network Overview</span></li>
      <li><span class="toc-num">4</span><span>Configuration Backup Completeness</span></li>
      <li><span class="toc-num">5</span><span>Device Health &amp; Inventory</span></li>
      <li><span class="toc-num">6</span><span>Sites, Networks, VLANs, and DHCP</span></li>
      <li><span class="toc-num">7</span><span>WiFi and Client Visibility</span></li>
      <li><span class="toc-num">8</span><span>Security Baseline</span></li>
      <li><span class="toc-num">9</span><span>Firewall and Policy Backup</span></li>
      <li><span class="toc-num">10</span><span>Raw Backup Files</span></li>
    </ol>
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
