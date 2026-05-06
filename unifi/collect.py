#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .client import UniFiClient, UniFiRequestError
from .env import load_env


ROOT = Path(__file__).resolve().parents[1]
LOCAL_NETWORK_PREFIX = "/proxy/network/integration/v1"
SOURCE_NOTES = [
    {
        "name": "Official UniFi API overview",
        "url": "https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API",
        "note": "Ubiquiti documents Site Manager and local application APIs as separate surfaces.",
    },
    {
        "name": "Official Site Manager API",
        "url": "https://developer.ui.com/site-manager-api/",
        "note": "Cloud API for high-level host, site, device, ISP, and SD-WAN visibility.",
    },
]
OPTIONAL_404_SITE_ENDPOINTS = {
    "vpn_tunnels": "This UniFi Network version does not expose VPN tunnel listing through the Network Integration API.",
}
TELEMETRY_PROBES: Tuple[Dict[str, str], ...] = (
    {"label": "site_ports", "scope": "site", "suffix": "ports", "purpose": "Per-site switch port telemetry"},
    {"label": "site_radios", "scope": "site", "suffix": "radios", "purpose": "Per-site AP radio telemetry"},
    {"label": "site_interfaces", "scope": "site", "suffix": "interfaces", "purpose": "Per-site interface telemetry"},
    {"label": "device_interfaces", "scope": "site", "suffix": "device-interfaces", "purpose": "Per-site device interface telemetry"},
    {"label": "switch_ports", "scope": "site", "suffix": "switch/ports", "purpose": "Switch port telemetry"},
    {"label": "wireless_radios", "scope": "site", "suffix": "wireless/radios", "purpose": "Wireless radio telemetry"},
    {"label": "wifi_radio_settings", "scope": "site", "suffix": "wifi/radio-settings", "purpose": "WiFi radio settings"},
    {"label": "wifi_rf_environments", "scope": "site", "suffix": "wifi/rf-environments", "purpose": "RF environment telemetry"},
    {"label": "wifi_channel_plans", "scope": "site", "suffix": "wifi/channel-plans", "purpose": "Channel plan telemetry"},
    {"label": "device_ports", "scope": "device", "interface": "ports", "suffix": "devices/{device_id}/ports", "purpose": "Per-device port telemetry"},
    {"label": "device_radios", "scope": "device", "interface": "radios", "suffix": "devices/{device_id}/radios", "purpose": "Per-device radio telemetry"},
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _bool_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _safe_name(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return clean.strip("_") or "site"


def _safe_label(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return clean.strip("_") or "item"


def _items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


def _site_id(site: Dict[str, Any]) -> str:
    return str(site.get("id") or site.get("siteId") or site.get("_id") or site.get("internalReference") or "")


def _site_name(site: Dict[str, Any]) -> str:
    meta = site.get("meta") if isinstance(site.get("meta"), dict) else {}
    return str(site.get("name") or meta.get("name") or site.get("description") or _site_id(site) or "Default")


def _site_matches(site: Dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    wanted = selector.strip().lower()
    values = {
        str(site.get("id") or ""),
        str(site.get("siteId") or ""),
        str(site.get("_id") or ""),
        str(site.get("internalReference") or ""),
        str(site.get("name") or ""),
        _site_name(site),
    }
    return wanted in {value.strip().lower() for value in values if value}


def _device_with_interface(devices: Iterable[Dict[str, Any]], interface: str) -> Dict[str, Any] | None:
    wanted = interface.strip().lower()
    for device in devices:
        interfaces = device.get("interfaces")
        if not isinstance(interfaces, list):
            continue
        available = {str(item).strip().lower() for item in interfaces if item}
        if wanted in available and (device.get("id") or device.get("_id")):
            return device
    return None


def _payload_count(payload: Any) -> int:
    items = _items(payload)
    if items:
        return len(items)
    if payload in (None, ""):
        return 0
    if isinstance(payload, dict):
        return 1
    if isinstance(payload, list):
        return len(payload)
    return 1


def _call_list(
    client: UniFiClient,
    path: str,
    *,
    style: str,
    label: str,
    errors: List[Dict[str, Any]],
    unsupported: List[Dict[str, Any]] | None = None,
    optional_404_note: str = "",
) -> List[Any]:
    try:
        return client.paged_get(path, style=style)
    except UniFiRequestError as exc:
        record = {"label": label, "path": path, "status": exc.status, "error": str(exc)}
        if exc.status == 404 and unsupported is not None and optional_404_note:
            unsupported.append({**record, "note": optional_404_note})
        else:
            errors.append(record)
    except Exception as exc:
        errors.append({"label": label, "path": path, "status": None, "error": str(exc)})
    return []


def _probe_telemetry_endpoint(client: UniFiClient, path: str, *, label: str, purpose: str, output: Path, safe: str) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "label": label,
        "purpose": purpose,
        "path": path,
        "available": False,
        "status": None,
        "itemCount": 0,
    }
    try:
        payload = client.get_json(path, {"limit": 10, "offset": 0})
    except UniFiRequestError as exc:
        record.update({"status": exc.status, "error": str(exc)})
        return record
    except Exception as exc:
        record.update({"error": str(exc)})
        return record

    rel = f"sites/{safe}/telemetry/{_safe_label(label)}.json"
    _write_json(output / rel, payload)
    record.update({"available": True, "status": 200, "itemCount": _payload_count(payload), "file": rel})
    return record


def _collect_telemetry_probes(client: UniFiClient, network_prefix: str, site_id: str, safe: str, devices: Iterable[Dict[str, Any]], output: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    device_items = list(devices)
    for probe in TELEMETRY_PROBES:
        label = probe["label"]
        suffix = probe["suffix"]
        if probe.get("scope") == "device":
            device = _device_with_interface(device_items, probe.get("interface", ""))
            if not device:
                results.append(
                    {
                        "label": label,
                        "purpose": probe.get("purpose", ""),
                        "path": "",
                        "available": False,
                        "status": None,
                        "itemCount": 0,
                        "note": f"No sampled device advertises {probe.get('interface')} interface capability.",
                    }
                )
                continue
            device_id = str(device.get("id") or device.get("_id"))
            suffix = suffix.format(device_id=device_id)
        path = f"{network_prefix}/sites/{site_id}/{suffix}"
        results.append(_probe_telemetry_endpoint(client, path, label=label, purpose=probe.get("purpose", ""), output=output, safe=safe))
    return results


def collect_site_manager(output: Path) -> Dict[str, Any]:
    api_key = os.getenv("UNIFI_SITE_MANAGER_API_KEY") or os.getenv("UNIFI_API_KEY")
    if not api_key:
        return {"enabled": False, "reason": "UNIFI_SITE_MANAGER_API_KEY or UNIFI_API_KEY is not set"}

    client = UniFiClient(
        os.getenv("UNIFI_SITE_MANAGER_BASE_URL", "https://api.ui.com"),
        api_key,
        timeout=int(os.getenv("UNIFI_REQUEST_TIMEOUT", "30")),
        verify_ssl=True,
    )
    errors: List[Dict[str, Any]] = []
    endpoints = {
        "hosts": "/v1/hosts",
        "sites": "/v1/sites",
        "devices": "/v1/devices",
        "sd_wan_configs": "/v1/sd-wan-configs",
    }
    summary: Dict[str, Any] = {"enabled": True, "baseUrl": client.base_url, "files": {}, "counts": {}, "errors": errors}
    for label, path in endpoints.items():
        data = _call_list(client, path, style="nextToken", label=f"site_manager_{label}", errors=errors)
        rel = f"site_manager_{label}.json"
        _write_json(output / rel, data)
        summary["files"][label] = rel
        summary["counts"][label] = len(data)
    return summary


def _fatal_auth_errors(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    fatal: List[Dict[str, Any]] = []
    for surface in ("siteManager", "networkApplication"):
        payload = summary.get(surface)
        if not isinstance(payload, dict) or not payload.get("enabled"):
            continue
        for error in payload.get("errors") or []:
            if not isinstance(error, dict):
                continue
            if error.get("label") in {"site_manager_sites", "network_sites"} and error.get("status") in {401, 403}:
                fatal.append({"surface": surface, **error})
    return fatal


def _fatal_connectivity_errors(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    fatal: List[Dict[str, Any]] = []
    for surface in ("siteManager", "networkApplication"):
        payload = summary.get(surface)
        if not isinstance(payload, dict) or not payload.get("enabled"):
            continue
        for error in payload.get("errors") or []:
            if not isinstance(error, dict):
                continue
            if error.get("label") in {"site_manager_sites", "network_sites"} and error.get("status") is None:
                fatal.append({"surface": surface, **error})
    return fatal


def collect_network_application(output: Path, selected_site_id: str = "", console_id: str = "") -> Dict[str, Any]:
    api_key = os.getenv("UNIFI_NETWORK_API_KEY") or os.getenv("UNIFI_API_KEY")
    base_url = os.getenv("UNIFI_NETWORK_BASE_URL") or os.getenv("UNIFI_BASE_URL")
    console_id = console_id or os.getenv("UNIFI_NETWORK_CONSOLE_ID", "")
    if not api_key:
        return {"enabled": False, "reason": "UNIFI_NETWORK_API_KEY is not set"}
    if not base_url and not console_id:
        return {"enabled": False, "reason": "Set UNIFI_NETWORK_BASE_URL for local access or UNIFI_NETWORK_CONSOLE_ID for remote connector access"}

    connection_type = "remote" if console_id and not base_url else "local"
    if connection_type == "remote":
        base_url = os.getenv("UNIFI_NETWORK_REMOTE_BASE_URL", "https://api.ui.com")
        network_prefix = f"/v1/connector/consoles/{console_id}/network/integration/v1"
        verify_ssl = True
    else:
        network_prefix = LOCAL_NETWORK_PREFIX
        verify_ssl = _bool_env("UNIFI_VERIFY_SSL", False)

    client = UniFiClient(
        base_url or "",
        api_key,
        timeout=int(os.getenv("UNIFI_REQUEST_TIMEOUT", "30")),
        verify_ssl=verify_ssl,
    )
    errors: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "enabled": True,
        "baseUrl": client.base_url,
        "connectionType": connection_type,
        "consoleId": console_id or None,
        "verifySsl": client.verify_ssl,
        "files": {},
        "counts": {},
        "errors": errors,
        "unsupportedEndpoints": unsupported,
    }

    try:
        info = client.get_json(f"{network_prefix}/info")
    except UniFiRequestError as exc:
        info = {"error": str(exc), "status": exc.status}
        errors.append({"label": "network_info", "path": f"{network_prefix}/info", "status": exc.status, "error": str(exc)})
    _write_json(output / "network_info.json", info)
    summary["files"]["info"] = "network_info.json"

    sites = _call_list(client, f"{network_prefix}/sites", style="offset", label="network_sites", errors=errors)
    if selected_site_id:
        sites = [site for site in _items(sites) if _site_matches(site, selected_site_id)]
    _write_json(output / "network_sites.json", sites)
    summary["files"]["sites"] = "network_sites.json"
    summary["counts"]["sites"] = len(sites)

    site_endpoints: Iterable[Tuple[str, str]] = (
        ("devices", "devices"),
        ("clients", "clients"),
        ("networks", "networks"),
        ("wifi", "wifi/broadcasts"),
        ("hotspot_vouchers", "hotspot/vouchers"),
        ("firewall_zones", "firewall/zones"),
        ("firewall_policies", "firewall/policies"),
        ("acl_rules", "acl-rules"),
        ("traffic_lists", "traffic-matching-lists"),
        ("wans", "wans"),
        ("vpn_servers", "vpn/servers"),
        ("vpn_tunnels", "vpn/tunnels"),
        ("radius", "radius/profiles"),
        ("dns_policies", "dns/policies"),
    )

    site_summaries: List[Dict[str, Any]] = []
    for site in _items(sites):
        sid = _site_id(site)
        if not sid and selected_site_id:
            sid = selected_site_id
        name = _site_name(site)
        safe = _safe_name(name or sid)
        site_summary: Dict[str, Any] = {"id": sid, "name": name, "files": {}, "counts": {}}
        site_payloads: Dict[str, List[Any]] = {}
        for label, suffix in site_endpoints:
            path = f"{network_prefix}/sites/{sid}/{suffix}"
            data = _call_list(
                client,
                path,
                style="offset",
                label=f"{name}:{label}",
                errors=errors,
                unsupported=unsupported,
                optional_404_note=OPTIONAL_404_SITE_ENDPOINTS.get(label, ""),
            )
            rel = f"sites/{safe}/{label}.json"
            _write_json(output / rel, data)
            site_summary["files"][label] = rel
            site_summary["counts"][label] = len(data)
            site_payloads[label] = data
        telemetry_probe = _collect_telemetry_probes(client, network_prefix, sid, safe, _items(site_payloads.get("devices", [])), output)
        telemetry_rel = f"sites/{safe}/telemetry_probe.json"
        _write_json(output / telemetry_rel, telemetry_probe)
        site_summary["files"]["telemetry_probe"] = telemetry_rel
        site_summary["counts"]["telemetry_probe_available"] = sum(1 for result in telemetry_probe if result.get("available"))
        site_summary["counts"]["telemetry_probe_total"] = len(telemetry_probe)
        site_summaries.append(site_summary)

    _write_json(output / "network_site_summaries.json", site_summaries)
    summary["files"]["site_summaries"] = "network_site_summaries.json"
    summary["siteSummaries"] = site_summaries
    return summary


def main(argv: List[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Collect UniFi Site Manager and Network Application data.")
    parser.add_argument("--mode", choices=["auto", "site-manager", "network", "both"], default=os.getenv("UNIFI_COLLECTION_MODE", "auto"))
    parser.add_argument("--site-id", default=os.getenv("UNIFI_SITE_ID", ""))
    parser.add_argument("--console-id", default=os.getenv("UNIFI_NETWORK_CONSOLE_ID", ""))
    parser.add_argument("--output-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    args = parser.parse_args(argv)

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    mode = args.mode
    if mode == "auto":
        has_network = bool(
            (os.getenv("UNIFI_NETWORK_API_KEY") or os.getenv("UNIFI_API_KEY"))
            and (
                os.getenv("UNIFI_NETWORK_BASE_URL")
                or os.getenv("UNIFI_BASE_URL")
                or args.console_id
                or os.getenv("UNIFI_NETWORK_CONSOLE_ID")
            )
        )
        has_site_manager = bool(os.getenv("UNIFI_SITE_MANAGER_API_KEY") or os.getenv("UNIFI_API_KEY"))
        if has_network and has_site_manager:
            mode = "both"
        elif has_network:
            mode = "network"
        elif has_site_manager:
            mode = "site-manager"
        else:
            print("Missing UniFi API configuration.", file=sys.stderr)
            print("Set UNIFI_NETWORK_BASE_URL or UNIFI_NETWORK_CONSOLE_ID with UNIFI_NETWORK_API_KEY, or set UNIFI_SITE_MANAGER_API_KEY.", file=sys.stderr)
            return 1

    metadata: Dict[str, Any] = {
        "collectedAt": datetime.now().isoformat(timespec="seconds"),
        "requestedMode": args.mode,
        "effectiveMode": mode,
        "sourceNotes": SOURCE_NOTES,
        "siteIdFilter": args.site_id or None,
        "consoleId": args.console_id or None,
    }
    summary: Dict[str, Any] = {"metadata": metadata}
    if mode in {"site-manager", "both"}:
        summary["siteManager"] = collect_site_manager(output)
    if mode in {"network", "both"}:
        summary["networkApplication"] = collect_network_application(output, args.site_id, args.console_id)

    _write_json(output / "collection_summary.json", summary)
    print(f"Collected UniFi data into {output}")
    print(json.dumps(summary, indent=2))
    fatal = _fatal_auth_errors(summary)
    if fatal:
        print("Fatal UniFi authorization failure on required site-discovery endpoint.", file=sys.stderr)
        for err in fatal:
            print(f"- {err.get('surface')} {err.get('label')}: HTTP {err.get('status')}", file=sys.stderr)
        return 1
    fatal = _fatal_connectivity_errors(summary)
    if fatal:
        print("Fatal UniFi connectivity failure on required site-discovery endpoint.", file=sys.stderr)
        for err in fatal:
            print(f"- {err.get('surface')} {err.get('label')}: {err.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
