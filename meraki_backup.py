#!/usr/bin/env python3
import argparse
import json
import logging
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone
from typing import Callable, TextIO
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger(__name__)

from meraki_env import load_env
from meraki_client import MerakiRequestError, get_json as _client_get_json, get_one as _client_get_one, paged_get as _client_paged_get

API_BASE          = os.getenv("MERAKI_API_BASE", "https://api.meraki.com/api/v1")
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR       = os.path.join(BASE_DIR, "backups")
_REQUEST_TIMEOUT  = int(os.getenv("MERAKI_REQUEST_TIMEOUT", "30"))   # seconds per call
_MAX_RETRIES      = int(os.getenv("MERAKI_MAX_RETRIES",     "5"))    # 429 retries

# ── Schema versioning ────────────────────────────────────────────────────────
# Increment when backup file shapes change (new keys, renamed files, type changes).
# report_generator.py compares against EXPECTED_BACKUP_SCHEMA_VERSION in common.py.
BACKUP_SCHEMA_VERSION = 1
PIPELINE_VERSION      = "1.2"

# ── Timespan / pagination constants ─────────────────────────────────────────
TIMESPAN_1H   =   3_600   # seconds
TIMESPAN_24H  =  86_400   # seconds
TIMESPAN_7D   = 604_800   # seconds
TIMESPAN_31D  = 2_678_400 # seconds
PER_PAGE_DEFAULT = 500
PER_PAGE_EVENTS  = 100
RESOLUTION_1H = 3_600


def _org_slug(name: str) -> str:
    """Turn an org display name into a safe directory name, e.g. 'Acme Corp' → 'Acme_Corp'."""
    slug = re.sub(r"[^\w\s\-]", "", name)          # strip special chars
    slug = re.sub(r"[\s\-]+", "_", slug.strip())   # spaces/hyphens → _
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unknown_org"


def _safe_org_dir(base: str, slug: str) -> str:
    """Return the resolved org directory path, raising ValueError if it escapes base."""
    base_path = pathlib.Path(base).resolve()
    org_path  = (base_path / slug).resolve()
    if not str(org_path).startswith(str(base_path) + os.sep):
        raise ValueError(
            f"Org slug {slug!r} resolves outside backup directory ({org_path})"
        )
    return str(org_path)


def log_line(log_f: TextIO, level: str, message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_f.write(f"{ts} [{level}] {message}\n")
    log_f.flush()


def _get_json(url: str, api_key: str) -> Dict[str, Any]:
    return _client_get_json(url, api_key, timeout=_REQUEST_TIMEOUT)


def paged_get(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
    return _client_paged_get(
        path,
        api_key,
        params=params,
        per_page_default=PER_PAGE_DEFAULT,
        max_retries=_MAX_RETRIES,
        courtesy_delay=0.2,
    )


def get_one(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return _client_get_one(path, api_key, params=params)


def safe_paged_get(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Tuple[List[Any], Optional[str]]:
    try:
        return paged_get(path, api_key, params=params), None
    except Exception as e:
        return [], str(e)


def safe_get_one(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Any, Optional[str]]:
    try:
        return get_one(path, api_key, params=params), None
    except Exception as e:
        return None, str(e)


def is_capability_error(err: Optional[str]) -> bool:
    if not err:
        return False
    text = err.lower()
    return (
        "http 404" in text
        or "no mr repeaters found" in text
        or "not supported" in text
    )


def fetch_licensing_overview(org_id: str, api_key: str) -> Tuple[Any, Optional[str]]:
    licensing, err = safe_get_one(f"/organizations/{org_id}/licensing/overview", api_key)
    if not err:
        return licensing, None

    if "http 404" not in err.lower():
        return licensing, err

    # Older co-term orgs can expose license information on the coterm endpoint instead.
    coterm, coterm_err = safe_get_one(
        f"/organizations/{org_id}/licensing/coterm/licenses",
        api_key,
    )
    if coterm_err:
        return None, f"{err}; fallback failed: {coterm_err}"

    return {"licenseMode": "co-term", "licenses": coterm}, None


def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _cache_is_fresh(path: str, max_age_h: float = 12.0, force: bool = False) -> bool:
    """Return True if path exists, is readable JSON, and is younger than max_age_h hours."""
    if force or not os.path.exists(path):
        return False
    age_h = (time.time() - os.path.getmtime(path)) / 3600.0
    if age_h >= max_age_h:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        return False


def _load_json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _artifact_dir(org_dir: str, category: str) -> str:
    path = os.path.join(org_dir, category)
    os.makedirs(path, exist_ok=True)
    return path


def _artifact_path(org_dir: str, category: str, item_id: str, filename: str) -> str:
    safe_item = re.sub(r"[^\w.\-]+", "_", str(item_id)).strip("._") or "unknown"
    return os.path.join(_artifact_dir(org_dir, category), safe_item, filename)


def _write_granular_json(org_dir: str, category: str, item_id: str, filename: str, payload: Any) -> None:
    path = _artifact_path(org_dir, category, item_id, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json(path, payload)


def _read_granular_json(org_dir: str, category: str, item_id: str, filename: str) -> Any:
    path = _artifact_path(org_dir, category, item_id, filename)
    if not os.path.exists(path):
        return None
    return _load_json_file(path)


def _granular_cache_fresh(
    org_dir: str,
    category: str,
    item_id: str,
    filename: str,
    max_age_h: float,
    force: bool = False,
) -> bool:
    return _cache_is_fresh(
        _artifact_path(org_dir, category, item_id, filename),
        max_age_h=max_age_h,
        force=force,
    )


def load_devices_by_type(inventory: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for d in inventory:
        ptype = d.get("productType") or "unknown"
        by_type.setdefault(ptype, []).append(d)
    return by_type


def recommend_switch_ports(
    port_statuses: Dict[str, List[Dict[str, Any]]],
    port_configs: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    findings = []
    low_speed_uplinks = 0
    uplinks_total = 0

    configs_by_serial_port: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for serial, configs in port_configs.items():
        port_map: Dict[str, Dict[str, Any]] = {}
        for cfg in configs:
            pid = str(cfg.get("portId")) if cfg.get("portId") is not None else None
            if pid:
                port_map[pid] = cfg
        configs_by_serial_port[serial] = port_map

    def _meaningful_port_messages(messages: List[str], is_uplink: bool) -> List[str]:
        benign_fragments = ("disconnected", "not connected", "no link", "link down", "down")
        result = []
        for message in messages:
            text = str(message or "").strip()
            if not text:
                continue
            if not is_uplink and any(fragment in text.lower() for fragment in benign_fragments):
                continue
            result.append(text)
        return result

    for serial, ports in port_statuses.items():
        cfg_map = configs_by_serial_port.get(serial, {})
        for p in ports:
            if p.get("isUplink"):
                uplinks_total += 1
                speed = p.get("speed") or ""
                status = p.get("status")
                if status != "Connected":
                    findings.append({
                        "serial": serial,
                        "portId": p.get("portId"),
                        "issue": "Uplink not connected",
                        "detail": status,
                    })
                if speed in ("10 Mbps", "100 Mbps"):
                    low_speed_uplinks += 1
                    findings.append({
                        "serial": serial,
                        "portId": p.get("portId"),
                        "issue": "Uplink speed low",
                        "detail": speed,
                    })
            port_id = str(p.get("portId")) if p.get("portId") is not None else None
            cfg = cfg_map.get(port_id or "", {})
            enabled = cfg.get("enabled", True)
            status = p.get("status")
            # Avoid flagging disconnected access ports that are disabled
            if enabled and status == "Disconnected" and p.get("isUplink"):
                findings.append({
                    "serial": serial,
                    "portId": p.get("portId"),
                    "issue": "Uplink disconnected",
                    "detail": "Disconnected",
                })
            errors = _meaningful_port_messages(p.get("errors") or [], bool(p.get("isUplink")))
            if errors:
                findings.append({
                    "serial": serial,
                    "portId": p.get("portId"),
                    "issue": "Port errors",
                    "detail": ", ".join(errors),
                })
            warnings = _meaningful_port_messages(p.get("warnings") or [], bool(p.get("isUplink")))
            if warnings:
                findings.append({
                    "serial": serial,
                    "portId": p.get("portId"),
                    "issue": "Port warnings",
                    "detail": ", ".join(warnings),
                })
    return {
        "switch_port_findings": findings,
        "summary": {
            "total_findings": len(findings),
            "uplinks_total": uplinks_total,
            "low_speed_uplinks": low_speed_uplinks,
        },
    }


def summarize_availabilities(avail: List[Dict[str, Any]]) -> Dict[str, Any]:
    offline = [d for d in avail if d.get("status") and d.get("status").lower() != "online"]
    offline_details = []
    for d in offline[:20]:
        offline_details.append({
            "name": d.get("name"),
            "serial": d.get("serial"),
            "model": d.get("model"),
            "productType": d.get("productType"),
            "status": d.get("status"),
            "lastReportedAt": d.get("lastReportedAt"),
            "networkId": d.get("networkId"),
        })
    def impact_score(d: Dict[str, Any]) -> int:
        p = (d.get("productType") or "").lower()
        status = (d.get("status") or "").lower()
        score = 1
        if p in ("switch", "appliance"):
            score = 3
        elif p in ("wireless",):
            score = 2
        elif p in ("camera", "sensor"):
            score = 1
        if status == "alerting":
            score += 1
        return score

    offline_by_impact = sorted(
        offline,
        key=lambda d: (impact_score(d), d.get("productType") or "", d.get("name") or ""),
        reverse=True,
    )[:20]
    offline_impact_details = []
    for d in offline_by_impact:
        offline_impact_details.append({
            "name": d.get("name"),
            "serial": d.get("serial"),
            "model": d.get("model"),
            "productType": d.get("productType"),
            "status": d.get("status"),
            "lastReportedAt": d.get("lastReportedAt"),
            "networkId": d.get("networkId"),
        })
    return {
        "total": len(avail),
        "offline_count": len(offline),
        "offline_devices": offline[:200],
        "offline_details": offline_details,
        "offline_impact_details": offline_impact_details,
    }


def summarize_wireless_connection_stats(stats_by_network: Dict[str, Any]) -> Dict[str, Any]:
    summary = []
    for net_id, data in stats_by_network.items():
        if not isinstance(data, list):
            continue
        for row in data:
            serial = row.get("serial")
            conn = row.get("connectionStats") or {}
            success = conn.get("success")
            total = conn.get("total")
            if success is not None and total:
                success_rate = round((success / total) * 100, 2)
                summary.append({
                    "networkId": net_id,
                    "serial": serial,
                    "successRate": success_rate,
                    "total": total,
                })
    summary_sorted = sorted(summary, key=lambda x: x["successRate"])
    return {
        "ap_success_rate": summary_sorted[:50],
    }


def summarize_channel_utilization(util_by_device: List[Dict[str, Any]]) -> Dict[str, Any]:
    high_util = []
    for row in util_by_device:
        total = row.get("utilizationTotal")
        non_wifi = row.get("utilizationNon80211")
        if total is None and row.get("utilization"):
            total = row.get("utilization")
        if total is None:
            continue
        if total >= 50 or (non_wifi is not None and non_wifi >= 20):
            high_util.append({
                "networkId": row.get("networkId"),
                "serial": row.get("serial"),
                "utilizationTotal": total,
                "utilizationNon80211": non_wifi,
            })
    high_util_sorted = sorted(high_util, key=lambda x: x.get("utilizationTotal") or 0, reverse=True)
    return {
        "high_util_ap": high_util_sorted[:50],
    }


def summarize_rf_profiles(rf_profiles_by_network: Dict[str, Any]) -> Dict[str, Any]:
    profiles = []
    for net_id, data in rf_profiles_by_network.items():
        if not isinstance(data, list):
            continue
        for p in data:
            profiles.append({
                "networkId": net_id,
                "name": p.get("name"),
                "bandSelection": p.get("bandSelectionType"),
                "minPower": p.get("minPower"),
                "maxPower": p.get("maxPower"),
                "minBitrate": p.get("minBitrate"),
                "channelWidth": p.get("channelWidth"),
                "perSsidSettings": p.get("perSsidSettings"),
            })
    return {
        "rf_profiles": profiles,
    }

def summarize_inventory(inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_model: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    for d in inventory:
        model = d.get("model") or "unknown"
        ptype = d.get("productType") or "unknown"
        by_model[model] = by_model.get(model, 0) + 1
        by_type[ptype] = by_type.get(ptype, 0) + 1
    top_models = sorted(by_model.items(), key=lambda x: x[1], reverse=True)[:15]
    return {
        "by_model": by_model,
        "by_type": by_type,
        "top_models": top_models,
    }


def summarize_ap_clients(clients_by_network: Dict[str, Any]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for net_id, data in clients_by_network.items():
        if not isinstance(data, list):
            continue
        for c in data:
            if c.get("recentDeviceConnection") not in (None, "Wireless"):
                continue
            serial = c.get("recentDeviceSerial") or c.get("recentDeviceSerialNumber")
            if not serial:
                continue
            counts[serial] = counts.get(serial, 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:50]
    return {
        "ap_client_counts": top,
    }

def summarize_poe_power(port_statuses: Dict[str, List[Dict[str, Any]]], timespan_seconds: int) -> Dict[str, Any]:
    hours = max(timespan_seconds / TIMESPAN_1H, 1.0)
    switch_totals = []
    port_totals = []
    for serial, ports in port_statuses.items():
        total_wh = 0.0
        for p in ports:
            wh = p.get("powerUsageInWh")
            if isinstance(wh, (int, float)):
                total_wh += wh
                if wh > 0:
                    port_totals.append({
                        "serial": serial,
                        "portId": p.get("portId"),
                        "powerUsageInWh": wh,
                    })
        if total_wh > 0:
            switch_totals.append({
                "serial": serial,
                "powerUsageInWh": round(total_wh, 2),
                "avgWatts": round(total_wh / hours, 2),
            })
    switch_totals_sorted = sorted(switch_totals, key=lambda x: x["powerUsageInWh"], reverse=True)
    port_totals_sorted = sorted(port_totals, key=lambda x: x["powerUsageInWh"], reverse=True)
    return {
        "switch_poe_totals": switch_totals_sorted[:20],
        "port_poe_totals": port_totals_sorted[:50],
    }


def summarize_appliance_security(
    appliance_baseline_by_network: Dict[str, Any],
    networks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    networks_by_id = {
        n.get("id"): n.get("name", n.get("id", "Unknown network"))
        for n in networks
        if n.get("id")
    }
    checks: List[Dict[str, Any]] = []
    summary = {"pass": 0, "fail": 0, "warning": 0, "info": 0}

    def add_check(
        network_id: str,
        check: str,
        status: str,
        description: str,
        remediation: str,
        observed: Optional[str] = None,
    ) -> None:
        normalized = status.lower()
        if normalized not in summary:
            normalized = "info"
        summary[normalized] += 1
        checks.append(
            {
                "networkId": network_id,
                "networkName": networks_by_id.get(network_id, network_id),
                "check": check,
                "status": status.title(),
                "description": description,
                "remediation": remediation,
                "observed": observed or "",
            }
        )

    for net_id, baseline in appliance_baseline_by_network.items():
        if not isinstance(baseline, dict):
            continue

        malware = baseline.get("malware")
        if isinstance(malware, dict) and not malware.get("error"):
            mode = str(malware.get("mode", "unknown")).lower()
            if mode in ("enabled", "prevention"):
                add_check(
                    net_id,
                    "Advanced Malware Protection",
                    "pass",
                    "AMP is enabled on the MX appliance.",
                    "Keep AMP enabled and review blocked events regularly.",
                    observed=mode,
                )
            else:
                add_check(
                    net_id,
                    "Advanced Malware Protection",
                    "fail",
                    "AMP is disabled or unsupported on the MX appliance.",
                    "Enable Advanced Malware Protection where licensing supports it.",
                    observed=mode,
                )

        intrusion = baseline.get("intrusion")
        if isinstance(intrusion, dict) and not intrusion.get("error"):
            mode = str(intrusion.get("mode", "unknown")).lower()
            if mode == "prevention":
                status = "pass"
                desc = "IDS/IPS is enforcing prevention mode."
                remediation = "Keep the ruleset current and review security center events."
            elif mode in ("detection", "enabled"):
                status = "warning"
                desc = "IDS/IPS is enabled but not blocking threats."
                remediation = "Move IDS/IPS to prevention mode unless a known exception blocks it."
            else:
                status = "fail"
                desc = "IDS/IPS is disabled or unsupported."
                remediation = "Enable intrusion detection and prevention on MX appliances."
            add_check(net_id, "Intrusion Prevention", status, desc, remediation, observed=mode)

        firewall_settings = baseline.get("firewallSettings")
        if isinstance(firewall_settings, dict) and not firewall_settings.get("error"):
            ipsg = (
                firewall_settings.get("spoofingProtection", {})
                .get("ipSourceGuard", {})
                .get("mode", "unknown")
            )
            mode = str(ipsg).lower()
            if mode == "block":
                status = "pass"
                desc = "IP source guard is enforcing spoof protection."
                remediation = "Keep spoof protection enabled on internet-facing MX networks."
            elif mode in ("log", "alert"):
                status = "warning"
                desc = "Spoof protection is monitoring but not blocking."
                remediation = "Move spoof protection to blocking mode after validating expected traffic."
            else:
                status = "fail"
                desc = "Spoof protection is disabled or not configured."
                remediation = "Enable spoof protection to reduce exposure from forged traffic."
            add_check(net_id, "Spoof Protection", status, desc, remediation, observed=mode)

        port_forwarding = baseline.get("portForwardingRules")
        if isinstance(port_forwarding, dict) and not port_forwarding.get("error"):
            rules = port_forwarding.get("rules") or []
            any_rules = []
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                allowed = rule.get("allowedIps") or []
                if allowed == ["any"] or allowed == "any":
                    any_rules.append(str(rule.get("publicPort", "?")))
            if any_rules:
                add_check(
                    net_id,
                    "Internet-Exposed Port Forwarding",
                    "fail",
                    f"Port forwarding rules expose services to any source on ports: {', '.join(any_rules)}.",
                    "Restrict allowed IPs or remove internet-facing forwards that are no longer required.",
                    observed=", ".join(any_rules),
                )
            else:
                add_check(
                    net_id,
                    "Internet-Exposed Port Forwarding",
                    "pass",
                    "No unrestricted internet-exposed port forwarding rules were found.",
                    "Review port forwarding rules periodically as applications change.",
                )

    return {"checks": checks, "summary": summary}


def build_recommendations(
    org_name: str,
    network_count: int,
    devices_by_type: Dict[str, List[Dict[str, Any]]],
    switch_findings: Dict[str, Any],
    availability_summary: Dict[str, Any],
    wireless_summary: Dict[str, Any],
    channel_summary: Dict[str, Any],
    rf_summary: Dict[str, Any],
    poe_summary: Dict[str, Any],
    inventory_summary: Dict[str, Any],
    ap_client_summary: Dict[str, Any],
    lldp_cdp_by_switch: Dict[str, Any],
    security_baseline: Optional[Dict[str, Any]] = None,
    licensing: Optional[Dict[str, Any]] = None,
    firmware: Optional[Dict[str, Any]] = None,
    uplink_statuses: Optional[List[Dict[str, Any]]] = None,
    ssids_by_network: Optional[Dict[str, Any]] = None,
    alerts_by_network: Optional[Dict[str, Any]] = None,
) -> str:
    lines = []
    lines.append(f"# Meraki Recommendations: {org_name}")
    lines.append("")
    lines.append("## Scope")
    lines.append(f"- Networks: {network_count}")
    lines.append(f"- Devices: {sum(len(v) for v in devices_by_type.values())}")
    lines.append(f"- Switches: {len(devices_by_type.get('switch', []))}")
    lines.append(f"- Wireless APs: {len(devices_by_type.get('wireless', []))}")
    lines.append(f"- Offline devices: {availability_summary.get('offline_count', 0)}")
    lines.append("")

    lines.append("## Executive Summary")
    summary_points = []
    if switch_findings.get("summary", {}).get("total_findings", 0) > 0:
        summary_points.append("Switch port issues detected (CRC errors, warnings, or uplink problems).")
    if availability_summary.get("offline_count", 0) > 0:
        summary_points.append("Offline devices detected; prioritize core and alerting devices.")
    if channel_summary.get("high_util_ap"):
        summary_points.append("High wireless channel utilization detected on some APs.")
    if poe_summary.get("switch_poe_totals"):
        summary_points.append("PoE draw is significant on top switches; verify power budgets.")
    sec_summary = (security_baseline or {}).get("summary", {}) if isinstance(security_baseline, dict) else {}
    if sec_summary.get("fail", 0) > 0:
        summary_points.append(
            f"MX security baseline checks found {sec_summary.get('fail', 0)} failing control(s)."
        )
    elif sec_summary.get("warning", 0) > 0:
        summary_points.append(
            f"MX security baseline checks found {sec_summary.get('warning', 0)} warning-level control(s)."
        )
    if not summary_points:
        summary_points.append("No critical issues detected in this snapshot.")
    for s in summary_points:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("## Inventory Summary")
    by_type = inventory_summary.get("by_type") or {}
    for k in sorted(by_type.keys()):
        lines.append(f"- {k}: {by_type[k]}")
    top_models = inventory_summary.get("top_models") or []
    if top_models:
        lines.append("")
        lines.append("### Top Models")
        for model, count in top_models[:10]:
            lines.append(f"- {model}: {count}")
    lines.append("")

    # Licensing
    if licensing and isinstance(licensing, dict) and not licensing.get("error"):
        lines.append("## License Status")
        status = licensing.get("status", "unknown")
        expiry = licensing.get("expirationDate", "unknown")
        lines.append(f"- License status: **{status}**")
        lines.append(f"- Expiration: {expiry}")
        if status.lower() in ("expired", "license required"):
            lines.append("- ⚠️ CRITICAL: Licenses are expired or required. Cloud-managed features will stop functioning.")
            lines.append("- Renew licenses immediately via the Meraki Dashboard or your Cisco account rep.")
        elif status.lower() == "ok":
            lines.append("- Licenses are current. No action required.")
        lines.append("")

    # Firmware upgrades
    if firmware and isinstance(firmware, dict) and not firmware.get("error"):
        lines.append("## Firmware Upgrade Status")
        upcoming = firmware.get("upcomingUpgrades") or []
        completed = firmware.get("completedUpgrades") or []
        if upcoming:
            lines.append(f"- {len(upcoming)} upgrade(s) scheduled or available:")
            for u in upcoming[:10]:
                prod = u.get("productType", "device")
                to_ver = u.get("toVersion", {}).get("firmware", "?")
                sched = u.get("scheduledFor") or "unscheduled"
                lines.append(f"  - {prod}: → {to_ver} (scheduled: {sched})")
            lines.append("- Verify upgrade windows are set during maintenance hours to minimise disruption.")
        elif completed:
            lines.append("- No pending firmware upgrades. All recent upgrades completed.")
        else:
            lines.append("- No firmware upgrade data available.")
        lines.append("")

    # WAN uplink health
    if uplink_statuses and isinstance(uplink_statuses, list):
        lines.append("## WAN Uplink Health (MX Appliances)")
        degraded = []
        for dev in uplink_statuses:
            for ul in dev.get("uplinks", []):
                if ul.get("status") not in ("active", "ready"):
                    degraded.append({
                        "serial": dev.get("serial"),
                        "networkId": dev.get("networkId"),
                        "interface": ul.get("interface"),
                        "status": ul.get("status"),
                        "ip": ul.get("ip"),
                    })
        if degraded:
            lines.append(f"- ⚠️ {len(degraded)} uplink(s) not in active/ready state:")
            for d in degraded[:10]:
                lines.append(
                    f"  - Serial {d['serial']} ({d['networkId']}) "
                    f"interface {d['interface']}: {d['status']} (IP: {d['ip']})"
                )
        else:
            lines.append(f"- All {len(uplink_statuses)} MX uplink(s) reporting active/ready. WAN health OK.")
        lines.append("")

    # Recent alerts
    if alerts_by_network and isinstance(alerts_by_network, dict):
        all_alerts = []
        for net_id, net_alerts in alerts_by_network.items():
            if isinstance(net_alerts, list):
                for a in net_alerts:
                    a["networkId"] = net_id
                    all_alerts.append(a)
        if all_alerts:
            lines.append("## Recent Alerts (Last 7 Days)")
            lines.append(f"- {len(all_alerts)} alert(s) triggered across all networks.")
            # Deduplicate by type
            alert_types: Dict[str, int] = {}
            for a in all_alerts:
                atype = a.get("type") or a.get("alertTypeId") or "unknown"
                alert_types[atype] = alert_types.get(atype, 0) + 1
            for atype, count in sorted(alert_types.items(), key=lambda x: x[1], reverse=True)[:10]:
                lines.append(f"  - {atype}: {count}×")
            lines.append("- Review the Meraki Dashboard Alert Inbox for full details and remediation steps.")
            lines.append("")

    # SSID security audit
    if ssids_by_network and isinstance(ssids_by_network, dict):
        open_ssids = []
        wpa_personal_ssids = []
        for net_id, ssids in ssids_by_network.items():
            if not isinstance(ssids, list):
                continue
            for s in ssids:
                if not s.get("enabled"):
                    continue
                auth = s.get("authMode", "")
                enc = s.get("encryptionMode", "")
                name = s.get("name", "?")
                if auth == "open":
                    open_ssids.append(f"  - Network {net_id}: '{name}' (open/no auth)")
                elif auth == "psk" and enc in ("wpa", "wpa2"):
                    wpa_personal_ssids.append(f"  - Network {net_id}: '{name}' (WPA-Personal PSK)")
        if open_ssids or wpa_personal_ssids:
            lines.append("## Wireless SSID Security")
            if open_ssids:
                lines.append(f"- ⚠️ {len(open_ssids)} open (unauthenticated) SSID(s) detected:")
                lines.extend(open_ssids[:10])
                lines.append("  - Consider adding a splash page or migrating to WPA2-Enterprise.")
            if wpa_personal_ssids:
                lines.append(f"- {len(wpa_personal_ssids)} WPA-Personal SSID(s) (PSK) detected:")
                lines.extend(wpa_personal_ssids[:10])
                lines.append("  - For enterprise environments, migrate to WPA2-Enterprise (802.1X) for per-user auth.")
            lines.append("")

    if security_baseline and isinstance(security_baseline, dict):
        checks = security_baseline.get("checks") or []
        if checks:
            lines.append("## MX Security Baseline")
            failing = [c for c in checks if str(c.get("status", "")).lower() == "fail"]
            warning = [c for c in checks if str(c.get("status", "")).lower() == "warning"]
            if failing:
                lines.append(f"- {len(failing)} failing baseline control(s) detected across MX networks.")
                for check in failing[:10]:
                    lines.append(
                        f"- {check.get('networkName')}: {check.get('check')} -> {check.get('description')}"
                    )
            if warning:
                lines.append(f"- {len(warning)} warning-level baseline control(s) detected.")
                for check in warning[:10]:
                    lines.append(
                        f"- {check.get('networkName')}: {check.get('check')} -> {check.get('description')}"
                    )
            if not failing and not warning:
                lines.append("- No failing MX security baseline controls were detected in the API snapshot.")
            lines.append("")

    findings = switch_findings.get("switch_port_findings", [])
    if findings:
        lines.append("## Switch Chain And Port Health")
        lines.append("- Review uplink connectivity and speed for switches with flagged uplinks.")
        lines.append("- Investigate ports with errors or warnings; check cabling, negotiation, STP, and PoE loads.")
        lines.append("")
        lines.append("### Flagged Ports")
        for f in findings[:200]:
            lines.append(f"- Serial {f.get('serial')} Port {f.get('portId')}: {f.get('issue')} ({f.get('detail')})")
        if len(findings) > 200:
            lines.append(f"- ... {len(findings) - 200} more findings omitted")
        lines.append("")
    else:
        lines.append("## Switch Chain And Port Health")
        lines.append("- No switch port errors, warnings, or low-speed uplinks were detected in the API snapshot.")
        lines.append("")

    lines.append("## Availability")
    if availability_summary.get("offline_count", 0) > 0:
        lines.append("- Investigate offline devices; check power, cabling, and upstream connectivity.")
        lines.append(f"- Offline devices in snapshot: {availability_summary.get('offline_count')}")
        lines.append("")
        lines.append("### Offline Device Details (Top 20)")
        for d in availability_summary.get("offline_details", []):
            lines.append(
                f"- {d.get('name') or d.get('serial')} ({d.get('model') or d.get('productType')}), "
                f"status {d.get('status')}, last reported {d.get('lastReportedAt')}, network {d.get('networkId')}"
            )
        lines.append("")
        lines.append("### Offline Devices By Impact (Top 20)")
        for d in availability_summary.get("offline_impact_details", []):
            lines.append(
                f"- {d.get('name') or d.get('serial')} ({d.get('model') or d.get('productType')}), "
                f"status {d.get('status')}, last reported {d.get('lastReportedAt')}, network {d.get('networkId')}"
            )
    else:
        lines.append("- All devices reported online in the API snapshot.")
    lines.append("")

    lines.append("## Capacity And Bottlenecks")
    lines.append("- If any uplinks are running at 100 Mbps or 10 Mbps, upgrade cabling or port negotiation to 1 Gbps or higher.")
    lines.append("- If core or distribution switches are at lower speeds than access layer uplinks, move higher-capacity switches up the chain.")
    lines.append("- For dense client areas, ensure switches and uplinks match aggregate client throughput demands.")
    lines.append("")

    lines.append("## Purchasing And Upgrade Targets")
    lines.append("- Prioritize replacing/recabling CRC‑error links before expanding capacity.")
    lines.append("- If core/distribution switches are older or lower‑capacity than access layer uplinks, plan upgrades there first.")
    lines.append("- For high‑density areas, budget for additional APs if channel utilization remains high after tuning.")
    lines.append("")

    lines.append("## Wireless AP Placement And Power")
    ap_rates = wireless_summary.get("ap_success_rate") or []
    if ap_rates:
        lines.append("- Review APs with low connection success rates; this can indicate RF interference, channel overlap, or power imbalance.")
        lines.append("")
        lines.append("### APs With Low Connection Success Rate")
        for row in ap_rates[:10]:
            lines.append(f"- Network {row.get('networkId')} AP {row.get('serial')}: {row.get('successRate')}% success over {row.get('total')} attempts")
    else:
        lines.append("- Wireless connection stats were not available or returned empty for this snapshot.")
    high_util = channel_summary.get("high_util_ap") or []
    if high_util:
        lines.append("")
        lines.append("### APs With High Channel Utilization")
        for row in high_util[:10]:
            lines.append(
                f"- Network {row.get('networkId')} AP {row.get('serial')}: "
                f"utilization {row.get('utilizationTotal')}% "
                f"(non-802.11 {row.get('utilizationNon80211')}%)"
            )
        lines.append("")
        lines.append("- Consider reducing transmit power, adjusting channels, or adding APs in high-utilization areas.")
    rf_profiles = rf_summary.get("rf_profiles") or []
    if rf_profiles:
        lines.append("")
        lines.append("### RF Profiles (Summary)")
        for p in rf_profiles[:10]:
            lines.append(
                f"- Network {p.get('networkId')} Profile {p.get('name')}: "
                f"band {p.get('bandSelection')}, power {p.get('minPower')}–{p.get('maxPower')}, "
                f"min bitrate {p.get('minBitrate')}, channel width {p.get('channelWidth')}"
            )
    lines.append("")

    ap_clients = ap_client_summary.get("ap_client_counts") or []
    if ap_clients:
        lines.append("## Wireless Client Load")
        lines.append("- Top APs by wireless client count (last 24 hours). Investigate if sustained high load.")
        for serial, count in ap_clients[:10]:
            lines.append(f"- AP {serial}: {count} clients")
        lines.append("")

    lines.append("## Cleanup And Removals")
    lines.append("- Identify dormant devices by low or no connectivity in availability data; consider decommissioning or repurposing.")
    lines.append("- Remove or reassign unused switch ports to reduce noise and speed troubleshooting.")
    lines.append("")

    poe_switches = poe_summary.get("switch_poe_totals") or []
    poe_ports = poe_summary.get("port_poe_totals") or []
    lines.append("## PoE Power")
    if poe_switches:
        lines.append("- Review PoE-heavy switches for budget headroom; check dashboard power budget vs draw.")
        lines.append("")
        lines.append("### Top Switches By PoE Energy (Last 24h)")
        for s in poe_switches[:10]:
            lines.append(f"- Switch {s.get('serial')}: {s.get('powerUsageInWh')} Wh (avg {s.get('avgWatts')} W)")
    if poe_ports:
        lines.append("")
        lines.append("### Top Ports By PoE Energy (Last 24h)")
        for p in poe_ports[:10]:
            neighbor = None
            sw_lldp = lldp_cdp_by_switch.get(p.get("serial")) or {}
            ports = sw_lldp.get("ports") if isinstance(sw_lldp, dict) else None
            if isinstance(ports, dict):
                port_info = ports.get(str(p.get("portId")))
                if isinstance(port_info, dict):
                    lldp = port_info.get("lldp") or {}
                    cdp = port_info.get("cdp") or {}
                    neighbor = lldp.get("systemName") or cdp.get("deviceId")
            if neighbor:
                lines.append(f"- Switch {p.get('serial')} Port {p.get('portId')}: {p.get('powerUsageInWh')} Wh (neighbor {neighbor})")
            else:
                lines.append(f"- Switch {p.get('serial')} Port {p.get('portId')}: {p.get('powerUsageInWh')} Wh")

    lines.append("")
    lines.append("## Next Actions")
    lines.append("1. Validate and remediate CRC error ports (cable/SFP/duplex).")
    lines.append("2. Restore or retire the offline core/critical devices.")
    lines.append("3. Review PoE budgets on top‑draw switches.")
    lines.append("4. If wireless issues persist, tune RF profiles and add APs in high‑utilization areas.")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Meraki backup pipeline — fetches org data and writes JSON artifacts."
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Ignore cached files and re-fetch all data from the Meraki API.",
    )
    parser.add_argument(
        "--cache-age",
        type=float,
        default=12.0,
        metavar="HOURS",
        help="Max age in hours for a cached file to be considered fresh (default: 12).",
    )
    args = parser.parse_args()
    force = args.force_refresh
    max_age_h = args.cache_age

    load_env()
    api_key = os.getenv("MERAKI_API_KEY")
    if not api_key:
        print("Missing MERAKI_API_KEY env var.", file=sys.stderr)
        return 1

    out_dir = BACKUPS_DIR
    os.makedirs(out_dir, exist_ok=True)

    if force:
        print("--force-refresh: ignoring all cached files.")

    log_path = os.path.join(out_dir, "backup.log")
    with open(log_path, "w", encoding="utf-8") as log_f:
        log_line(log_f, "INFO", f"Backup started. Output directory: {out_dir}")
        log_line(log_f, "INFO", f"force_refresh={force}  cache_age_h={max_age_h}")

        _orgs_path = os.path.join(out_dir, "organizations.json")
        if _cache_is_fresh(_orgs_path, max_age_h=max_age_h, force=force):
            orgs = _load_json_file(_orgs_path)
            log_line(log_f, "INFO", f"Organizations (cached, {len(orgs)} orgs)")
        else:
            orgs = paged_get("/organizations", api_key)
            write_json(_orgs_path, orgs)
            log_line(log_f, "INFO", f"Organizations fetched: {len(orgs)}")

        for org in orgs:
            org_id = org.get("id")
            org_name = org.get("name") or str(org_id)
            org_slug = _org_slug(org_name)
            try:
                org_dir = _safe_org_dir(out_dir, org_slug)
            except ValueError as e:
                log_line(log_f, "ERROR", f"Skipping org {org_name!r}: {e}")
                continue
            os.makedirs(org_dir, exist_ok=True)
            # Write human-readable name so other tools don't need to reverse the slug
            with open(os.path.join(org_dir, "org_name.txt"), "w", encoding="utf-8") as _nf:
                _nf.write(org_name)
            log_line(log_f, "INFO", f"Org start: {org_name} ({org_id}) → {org_slug}/")

            def _pf(filename: str) -> str:
                return os.path.join(org_dir, filename)

            def _cached_paged(filename: str, path_suffix: str, label: str) -> list:
                p = _pf(filename)
                if _cache_is_fresh(p, max_age_h=max_age_h, force=force):
                    data = _load_json_file(p)
                    log_line(log_f, "INFO", f"{label} (cached, {len(data) if isinstance(data,list) else '?'} items)")
                    return data
                data = paged_get(path_suffix, api_key)
                write_json(p, data)
                log_line(log_f, "INFO", f"{label}: {len(data)} items fetched")
                return data

            def _cached_safe_paged(filename: str, path_suffix: str, label: str) -> list:
                p = _pf(filename)
                if _cache_is_fresh(p, max_age_h=max_age_h, force=force):
                    data = _load_json_file(p)
                    log_line(log_f, "INFO", f"{label} (cached)")
                    return data if isinstance(data, list) else []
                data, err = safe_paged_get(path_suffix, api_key)
                write_json(p, data if not err else {"error": err})
                if err:
                    log_line(log_f, "WARN", f"{label} failed: {err}")
                else:
                    log_line(log_f, "INFO", f"{label}: {len(data)} items fetched")
                return data if not err else []

            def _cached_safe_get(filename: str, path_suffix: str, label: str, params=None) -> Any:
                p = _pf(filename)
                if _cache_is_fresh(p, max_age_h=max_age_h, force=force):
                    data = _load_json_file(p)
                    log_line(log_f, "INFO", f"{label} (cached)")
                    return data, None
                data, err = safe_get_one(path_suffix, api_key, params=params)
                write_json(p, data if not err else {"error": err})
                if err:
                    log_line(log_f, "WARN", f"{label} failed: {err}")
                else:
                    log_line(log_f, "INFO", f"{label} fetched")
                return data, err

            networks = _cached_paged("networks.json", f"/organizations/{org_id}/networks", "Networks")
            inventory = _cached_paged("inventory_devices.json", f"/organizations/{org_id}/inventory/devices", "Inventory")

            avail = _cached_safe_paged("devices_availabilities.json", f"/organizations/{org_id}/devices/availabilities", "Availabilities")
            availability_summary = summarize_availabilities(avail)

            devices_by_type = load_devices_by_type(inventory)
            inventory_summary = summarize_inventory(inventory)

            # Licensing overview
            _lic_path = _pf("licensing.json")
            if _cache_is_fresh(_lic_path, max_age_h=max_age_h, force=force):
                licensing = _load_json_file(_lic_path)
                err = licensing.get("error") if isinstance(licensing, dict) else None
                log_line(log_f, "INFO", f"Licensing (cached) for {org_name}")
            else:
                licensing, err = fetch_licensing_overview(str(org_id), api_key)
                write_json(_lic_path, licensing if not err else {"error": err})
                if err:
                    log_line(log_f, "WARN", f"Licensing failed for {org_name}: {err}")
                else:
                    log_line(log_f, "INFO", f"Licensing fetched for {org_name}")

            firmware, _fw_err = _cached_safe_get(
                "firmware_upgrades.json",
                f"/organizations/{org_id}/firmware/upgrades",
                "Firmware upgrades",
            )
            uplink_statuses = _cached_safe_paged(
                "uplink_statuses.json",
                f"/organizations/{org_id}/uplinks/statuses",
                "Uplink statuses",
            )
            devices_statuses = _cached_safe_paged(
                "devices_statuses.json",
                f"/organizations/{org_id}/devices/statuses",
                "Device statuses",
            )

            switches = devices_by_type.get("switch", [])
            _sw_stat_path = _pf("switch_port_statuses.json")
            _sw_cfg_path  = _pf("switch_port_configs.json")
            _lldp_path    = _pf("lldp_cdp.json")
            port_statuses = {}
            port_configs = {}
            lldp_cdp = {}
            if switches:
                log_line(log_f, "INFO", f"Collecting switch port data for {len(switches)} switch(es) in {org_name}")
            for idx, sw in enumerate(switches, start=1):
                serial = sw.get("serial")
                if not serial:
                    continue
                if idx == 1 or idx % 10 == 0 or idx == len(switches):
                    log_line(log_f, "INFO", f"Switch port progress for {org_name}: {idx}/{len(switches)} ({serial})")

                if _granular_cache_fresh(org_dir, "switches", serial, "port_statuses.json", max_age_h, force):
                    statuses = _read_granular_json(org_dir, "switches", serial, "port_statuses.json")
                else:
                    try:
                        statuses = paged_get(
                            f"/devices/{serial}/switch/ports/statuses",
                            api_key,
                            params={"timespan": TIMESPAN_24H},
                        )
                    except Exception as e:
                        statuses = [{"error": str(e)}]
                        log_line(log_f, "ERROR", f"Switch port statuses failed for {serial}: {e}")
                    _write_granular_json(org_dir, "switches", serial, "port_statuses.json", statuses)
                port_statuses[serial] = statuses if isinstance(statuses, list) else [{"error": "Invalid cached data"}]

                if _granular_cache_fresh(org_dir, "switches", serial, "port_configs.json", max_age_h, force):
                    configs = _read_granular_json(org_dir, "switches", serial, "port_configs.json")
                else:
                    try:
                        configs = paged_get(f"/devices/{serial}/switch/ports", api_key)
                    except Exception as e:
                        configs = [{"error": str(e)}]
                        log_line(log_f, "ERROR", f"Switch port configs failed for {serial}: {e}")
                    _write_granular_json(org_dir, "switches", serial, "port_configs.json", configs)
                port_configs[serial] = configs if isinstance(configs, list) else [{"error": "Invalid cached data"}]

                if _granular_cache_fresh(org_dir, "switches", serial, "lldp_cdp.json", max_age_h, force):
                    lldp = _read_granular_json(org_dir, "switches", serial, "lldp_cdp.json")
                else:
                    try:
                        lldp, err = safe_get_one(f"/devices/{serial}/lldpCdp", api_key)
                        lldp = lldp if not err else {"error": err}
                        if err:
                            log_line(log_f, "WARN", f"LLDP/CDP failed for {serial}: {err}")
                    except Exception as e:
                        lldp = {"error": str(e)}
                        log_line(log_f, "WARN", f"LLDP/CDP failed for {serial}: {e}")
                    _write_granular_json(org_dir, "switches", serial, "lldp_cdp.json", lldp)
                lldp_cdp[serial] = lldp if isinstance(lldp, dict) else {"error": "Invalid cached data"}

            write_json(_sw_stat_path, port_statuses)
            write_json(_sw_cfg_path,  port_configs)
            write_json(_lldp_path,    lldp_cdp)

            wireless_connection_stats = {}
            wireless_mesh_statuses = {}
            clients_overview = {}
            wireless_rf_profiles = {}
            wireless_rf_profile_assignments = {}
            wireless_settings = {}
            network_clients = {}
            wireless_clients = {}
            wireless_ssids = {}
            alerts_history = {}
            appliance_baseline = {}
            appliance_uplinks_usage = {}
            appliance_vlans = {}
            appliance_dhcp_subnets = {}
            appliance_policy_backup = {}
            appliances_by_network: Dict[str, List[Dict[str, Any]]] = {}
            for appliance in devices_by_type.get("appliance", []):
                net_id_for_appliance = appliance.get("networkId")
                if net_id_for_appliance:
                    appliances_by_network.setdefault(net_id_for_appliance, []).append(appliance)
            if networks:
                log_line(log_f, "INFO", f"Collecting network-level telemetry for {len(networks)} network(s) in {org_name}")
            _rf_assign_path = _pf("wireless_rf_profile_assignments.json")
            if _cache_is_fresh(_rf_assign_path, max_age_h=max_age_h, force=force):
                wireless_rf_profile_assignments = _load_json_file(_rf_assign_path)
                log_line(log_f, "INFO", f"Wireless RF profile assignments (cached) for {org_name}")
            else:
                wireless_rf_profile_assignments, rf_assign_err = safe_paged_get(
                    f"/organizations/{org_id}/wireless/rfProfiles/assignments/byDevice",
                    api_key,
                    params={"productTypes[]": ["wireless"]},
                )
                if rf_assign_err:
                    level = "INFO" if is_capability_error(rf_assign_err) else "WARN"
                    log_line(log_f, level, f"Wireless RF profile assignments unavailable for org {org_id}: {rf_assign_err}")
                    wireless_rf_profile_assignments = {"error": rf_assign_err}
                write_json(
                    _rf_assign_path,
                    wireless_rf_profile_assignments,
                )
            for idx, net in enumerate(networks, start=1):
                net_id = net.get("id")
                if not net_id:
                    continue
                net_name = net.get("name", net_id)
                log_line(log_f, "INFO", f"Network telemetry progress for {org_name}: {idx}/{len(networks)} ({net_name})")
                def _load_or_fetch_net(filename: str, fetcher: Callable[[], Tuple[Any, Optional[str]]], warn_label: str, capability_aware: bool = False) -> Any:
                    if _granular_cache_fresh(org_dir, "networks", net_id, filename, max_age_h, force):
                        return _read_granular_json(org_dir, "networks", net_id, filename)
                    data, err = fetcher()
                    payload = data if not err else {"error": err}
                    _write_granular_json(org_dir, "networks", net_id, filename, payload)
                    if err:
                        level = "INFO" if capability_aware and is_capability_error(err) else "WARN"
                        log_line(log_f, level, f"{warn_label} for network {net_id}: {err}")
                    return payload

                wireless_connection_stats[net_id] = _load_or_fetch_net(
                    "wireless_connection_stats.json",
                    lambda: safe_paged_get(
                        f"/networks/{net_id}/wireless/devices/connectionStats",
                        api_key,
                        params={"timespan": TIMESPAN_7D},
                    ),
                    "Wireless connectionStats failed",
                )
                wireless_mesh_statuses[net_id] = _load_or_fetch_net(
                    "wireless_mesh_statuses.json",
                    lambda: safe_get_one(
                        f"/networks/{net_id}/wireless/meshStatuses",
                        api_key,
                        params={"timespan": TIMESPAN_24H},
                    ),
                    "Wireless meshStatuses unavailable",
                    capability_aware=True,
                )
                clients_overview[net_id] = _load_or_fetch_net(
                    "clients_overview.json",
                    lambda: safe_get_one(
                        f"/networks/{net_id}/clients/overview",
                        api_key,
                        params={"timespan": TIMESPAN_24H},
                    ),
                    "Clients overview failed",
                )
                wireless_rf_profiles[net_id] = _load_or_fetch_net(
                    "wireless_rf_profiles.json",
                    lambda: safe_paged_get(f"/networks/{net_id}/wireless/rfProfiles", api_key),
                    "Wireless rfProfiles failed",
                )
                wireless_settings[net_id] = _load_or_fetch_net(
                    "wireless_settings.json",
                    lambda: safe_get_one(f"/networks/{net_id}/wireless/settings", api_key),
                    "Wireless settings unavailable",
                    capability_aware=True,
                )
                network_clients[net_id] = _load_or_fetch_net(
                    "network_clients.json",
                    lambda: safe_paged_get(
                        f"/networks/{net_id}/clients",
                        api_key,
                        params={"timespan": TIMESPAN_24H},
                    ),
                    "Network clients failed",
                )
                wireless_clients[net_id] = [
                    c for c in network_clients.get(net_id, [])
                    if isinstance(c, dict) and c.get("recentDeviceConnection") == "Wireless"
                ] if isinstance(network_clients.get(net_id), list) else network_clients.get(net_id, {})
                wireless_ssids[net_id] = _load_or_fetch_net(
                    "wireless_ssids.json",
                    lambda: safe_paged_get(f"/networks/{net_id}/wireless/ssids", api_key),
                    "SSIDs unavailable",
                    capability_aware=True,
                )
                alerts_history[net_id] = _load_or_fetch_net(
                    "alerts_history.json",
                    lambda: safe_paged_get(
                        f"/networks/{net_id}/alerts/history",
                        api_key,
                        params={"perPage": PER_PAGE_EVENTS},
                    ),
                    "Alerts history unavailable",
                    capability_aware=True,
                )

                if "appliance" in (net.get("productTypes") or []):
                    net_baseline: Dict[str, Any] = {}
                    policy_backup: Dict[str, Any] = {}
                    appliance_vlans[net_id] = _load_or_fetch_net(
                        "appliance_vlans.json",
                        lambda: safe_paged_get(f"/networks/{net_id}/appliance/vlans", api_key),
                        "Appliance VLANs unavailable",
                        capability_aware=True,
                    )
                    appliance_uplinks_usage[net_id] = _load_or_fetch_net(
                        "appliance_uplinks_usage.json",
                        lambda: safe_get_one(
                            f"/networks/{net_id}/appliance/uplinks/usageHistory",
                            api_key,
                            params={"timespan": TIMESPAN_7D, "resolution": RESOLUTION_1H},
                        ),
                        "Appliance uplink usage history unavailable",
                        capability_aware=True,
                    )

                    net_baseline["malware"] = _load_or_fetch_net(
                        "appliance_malware.json",
                        lambda: safe_get_one(f"/networks/{net_id}/appliance/security/malware", api_key),
                        "Appliance malware unavailable",
                        capability_aware=True,
                    )
                    net_baseline["intrusion"] = _load_or_fetch_net(
                        "appliance_intrusion.json",
                        lambda: safe_get_one(f"/networks/{net_id}/appliance/security/intrusion", api_key),
                        "Appliance intrusion unavailable",
                        capability_aware=True,
                    )
                    net_baseline["firewallSettings"] = _load_or_fetch_net(
                        "appliance_firewall_settings.json",
                        lambda: safe_get_one(f"/networks/{net_id}/appliance/firewall/settings", api_key),
                        "Appliance firewall settings unavailable",
                        capability_aware=True,
                    )
                    net_baseline["portForwardingRules"] = _load_or_fetch_net(
                        "appliance_port_forwarding_rules.json",
                        lambda: safe_get_one(f"/networks/{net_id}/appliance/firewall/portForwardingRules", api_key),
                        "Appliance port forwarding unavailable",
                        capability_aware=True,
                    )
                    policy_backup["portForwardingRules"] = net_baseline["portForwardingRules"]
                    policy_endpoints: List[Tuple[str, str, Callable[[], Tuple[Any, Optional[str]]], str]] = [
                        (
                            "l3FirewallRules",
                            "appliance_firewall_l3_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/l3FirewallRules", api_key
                            ),
                            "Appliance L3 firewall rules unavailable",
                        ),
                        (
                            "l7FirewallRules",
                            "appliance_firewall_l7_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/l7FirewallRules", api_key
                            ),
                            "Appliance L7 firewall rules unavailable",
                        ),
                        (
                            "inboundFirewallRules",
                            "appliance_firewall_inbound_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/inboundFirewallRules", api_key
                            ),
                            "Appliance inbound firewall rules unavailable",
                        ),
                        (
                            "cellularFirewallRules",
                            "appliance_firewall_cellular_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/cellularFirewallRules", api_key
                            ),
                            "Appliance cellular firewall rules unavailable",
                        ),
                        (
                            "inboundCellularFirewallRules",
                            "appliance_firewall_inbound_cellular_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/inboundCellularFirewallRules", api_key
                            ),
                            "Appliance inbound cellular firewall rules unavailable",
                        ),
                        (
                            "oneToOneNatRules",
                            "appliance_one_to_one_nat_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/oneToOneNatRules", api_key
                            ),
                            "Appliance 1:1 NAT rules unavailable",
                        ),
                        (
                            "oneToManyNatRules",
                            "appliance_one_to_many_nat_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/firewall/oneToManyNatRules", api_key
                            ),
                            "Appliance 1:Many NAT rules unavailable",
                        ),
                        (
                            "firewalledServices",
                            "appliance_firewalled_services.json",
                            lambda net_id=net_id: safe_paged_get(
                                f"/networks/{net_id}/appliance/firewall/firewalledServices", api_key
                            ),
                            "Appliance firewalled services unavailable",
                        ),
                        (
                            "contentFiltering",
                            "appliance_content_filtering.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/contentFiltering", api_key
                            ),
                            "Appliance content filtering unavailable",
                        ),
                        (
                            "trafficShapingRules",
                            "appliance_traffic_shaping_rules.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/trafficShaping/rules", api_key
                            ),
                            "Appliance traffic shaping rules unavailable",
                        ),
                        (
                            "siteToSiteVpn",
                            "appliance_site_to_site_vpn.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/appliance/vpn/siteToSiteVpn", api_key
                            ),
                            "Appliance site-to-site VPN unavailable",
                        ),
                        (
                            "groupPolicies",
                            "network_group_policies.json",
                            lambda net_id=net_id: safe_paged_get(
                                f"/networks/{net_id}/groupPolicies", api_key
                            ),
                            "Network group policies unavailable",
                        ),
                        (
                            "syslogServers",
                            "network_syslog_servers.json",
                            lambda net_id=net_id: safe_get_one(
                                f"/networks/{net_id}/syslogServers", api_key
                            ),
                            "Network syslog servers unavailable",
                        ),
                    ]
                    for key, filename, fetcher, warn_label in policy_endpoints:
                        policy_backup[key] = _load_or_fetch_net(
                            filename,
                            fetcher,
                            warn_label,
                            capability_aware=True,
                        )

                    appliance_baseline[net_id] = net_baseline
                    appliance_policy_backup[net_id] = policy_backup
                    _write_granular_json(org_dir, "networks", net_id, "appliance_policy_backup.json", policy_backup)
                    for appliance in appliances_by_network.get(net_id, []):
                        serial = appliance.get("serial")
                        if not serial:
                            continue
                        if _granular_cache_fresh(org_dir, "appliances", serial, "dhcp_subnets.json", max_age_h, force):
                            dhcp_subnets = _read_granular_json(org_dir, "appliances", serial, "dhcp_subnets.json")
                        else:
                            dhcp_subnets, dhcp_err = safe_paged_get(
                                f"/devices/{serial}/appliance/dhcp/subnets",
                                api_key,
                            )
                            dhcp_subnets = dhcp_subnets if not dhcp_err else {"error": dhcp_err}
                            _write_granular_json(org_dir, "appliances", serial, "dhcp_subnets.json", dhcp_subnets)
                            if dhcp_err:
                                level = "INFO" if is_capability_error(dhcp_err) else "WARN"
                                log_line(log_f, level, f"Appliance DHCP subnets unavailable for {serial}: {dhcp_err}")
                        appliance_dhcp_subnets[serial] = dhcp_subnets

            write_json(_pf("wireless_connection_stats.json"), wireless_connection_stats)
            write_json(_pf("wireless_mesh_statuses.json"), wireless_mesh_statuses)
            write_json(_pf("clients_overview.json"), clients_overview)
            write_json(_pf("wireless_rf_profiles.json"), wireless_rf_profiles)
            write_json(_pf("wireless_rf_profile_assignments.json"), wireless_rf_profile_assignments)
            write_json(_pf("wireless_settings.json"), wireless_settings)
            write_json(_pf("network_clients.json"), network_clients)
            write_json(_pf("wireless_clients.json"), wireless_clients)
            write_json(_pf("wireless_ssids.json"), wireless_ssids)
            write_json(_pf("alerts_history.json"), alerts_history)
            write_json(_pf("appliance_uplinks_usage.json"), appliance_uplinks_usage)
            write_json(_pf("appliance_vlans.json"), appliance_vlans)
            write_json(_pf("appliance_dhcp_subnets.json"), appliance_dhcp_subnets)
            write_json(_pf("appliance_policy_backup.json"), appliance_policy_backup)
            write_json(_pf("inventory_summary.json"), inventory_summary)
            _sb_path = _pf("security_baseline.json")
            if force or not _cache_is_fresh(_sb_path, max_age_h=max_age_h, force=False):
                security_baseline = summarize_appliance_security(appliance_baseline, networks)
                write_json(_sb_path, security_baseline)
            else:
                security_baseline = _load_json_file(_sb_path)
                log_line(log_f, "INFO", f"Security baseline (cached) for {org_name}")

            # Recommendations
            wireless_summary = summarize_wireless_connection_stats(wireless_connection_stats)
            rf_summary = summarize_rf_profiles(wireless_rf_profiles)
            ap_client_summary = summarize_ap_clients(network_clients)
            switch_findings = recommend_switch_ports(port_statuses, port_configs)
            poe_summary = summarize_poe_power(port_statuses, TIMESPAN_24H)
            _ch_path = _pf("channel_utilization_by_device.json")
            if _cache_is_fresh(_ch_path, max_age_h=max_age_h, force=force):
                channel_utilization = _load_json_file(_ch_path)
                err = None
                log_line(log_f, "INFO", f"Channel utilization (cached) for {org_name}")
            else:
                channel_utilization, err = safe_get_one(
                    f"/organizations/{org_id}/wireless/devices/channelUtilization/byDevice",
                    api_key,
                    params={
                        "networkIds[]": [n.get("id") for n in networks if n.get("id")],
                        "timespan": 86400,
                    },
                )
                if err:
                    log_line(log_f, "WARN", f"Channel utilization failed for org {org_id}: {err}")
                write_json(_ch_path, channel_utilization if not err else {"error": err})
            channel_data = channel_utilization if (not err and isinstance(channel_utilization, list)) else []
            channel_summary = summarize_channel_utilization(channel_data)
            write_json(_pf("poe_power_summary.json"), poe_summary)

            rec = build_recommendations(
                org_name,
                len(networks),
                devices_by_type,
                switch_findings,
                availability_summary,
                wireless_summary,
                channel_summary,
                rf_summary,
                poe_summary,
                inventory_summary,
                ap_client_summary,
                lldp_cdp,
                security_baseline=security_baseline,
                licensing=licensing if not isinstance(licensing, dict) or not licensing.get("error") else None,
                firmware=firmware if not isinstance(firmware, dict) or not firmware.get("error") else None,
                uplink_statuses=uplink_statuses if isinstance(uplink_statuses, list) else None,
                ssids_by_network=wireless_ssids,
                alerts_by_network=alerts_history,
            )
            with open(os.path.join(org_dir, "recommendations.md"), "w", encoding="utf-8") as f:
                f.write(rec)

            # Write schema meta — consumed by report_generator.py for version compatibility check
            write_json(os.path.join(org_dir, "backup_meta.json"), {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "pipeline_version": PIPELINE_VERSION,
                "backup_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "org_name": org_name,
                "org_id": str(org_id),
            })
            log_line(log_f, "INFO", f"Org complete: {org_name} ({org_id})")

        log_line(log_f, "INFO", "Backup completed successfully.")

    print(f"Backup complete. Output: {out_dir}/")
    for org in orgs:
        slug = _org_slug(org.get("name") or str(org.get("id")))
        print(f"  → {slug}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
