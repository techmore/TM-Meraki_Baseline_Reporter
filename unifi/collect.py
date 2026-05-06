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
NETWORK_PREFIX = "/proxy/network/integration/v1"
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


def _call_list(client: UniFiClient, path: str, *, style: str, label: str, errors: List[Dict[str, Any]]) -> List[Any]:
    try:
        return client.paged_get(path, style=style)
    except UniFiRequestError as exc:
        errors.append({"label": label, "path": path, "status": exc.status, "error": str(exc)})
    except Exception as exc:
        errors.append({"label": label, "path": path, "status": None, "error": str(exc)})
    return []


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


def collect_network_application(output: Path, selected_site_id: str = "") -> Dict[str, Any]:
    api_key = os.getenv("UNIFI_NETWORK_API_KEY") or os.getenv("UNIFI_API_KEY")
    base_url = os.getenv("UNIFI_NETWORK_BASE_URL") or os.getenv("UNIFI_BASE_URL")
    if not api_key or not base_url:
        return {"enabled": False, "reason": "UNIFI_NETWORK_BASE_URL and UNIFI_NETWORK_API_KEY are not set"}

    client = UniFiClient(
        base_url,
        api_key,
        timeout=int(os.getenv("UNIFI_REQUEST_TIMEOUT", "30")),
        verify_ssl=_bool_env("UNIFI_VERIFY_SSL", False),
    )
    errors: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "enabled": True,
        "baseUrl": client.base_url,
        "verifySsl": client.verify_ssl,
        "files": {},
        "counts": {},
        "errors": errors,
    }

    try:
        info = client.get_json(f"{NETWORK_PREFIX}/info")
    except UniFiRequestError as exc:
        info = {"error": str(exc), "status": exc.status}
        errors.append({"label": "network_info", "path": f"{NETWORK_PREFIX}/info", "status": exc.status, "error": str(exc)})
    _write_json(output / "network_info.json", info)
    summary["files"]["info"] = "network_info.json"

    sites = _call_list(client, f"{NETWORK_PREFIX}/sites", style="offset", label="network_sites", errors=errors)
    if selected_site_id:
        sites = [site for site in _items(sites) if _site_id(site) == selected_site_id]
    _write_json(output / "network_sites.json", sites)
    summary["files"]["sites"] = "network_sites.json"
    summary["counts"]["sites"] = len(sites)

    site_endpoints: Iterable[Tuple[str, str]] = (
        ("devices", "devices"),
        ("clients", "clients"),
        ("networks", "networks"),
        ("wifi", "wifi"),
        ("hotspot_vouchers", "hotspot/vouchers"),
        ("firewall_zones", "firewall/zones"),
        ("firewall_policies", "firewall/policies"),
        ("acl_rules", "acl-rules"),
        ("traffic_lists", "traffic-lists"),
        ("wans", "wans"),
        ("vpn_servers", "vpn-servers"),
        ("vpn_tunnels", "vpn-tunnels"),
        ("radius", "radius"),
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
        for label, suffix in site_endpoints:
            path = f"{NETWORK_PREFIX}/sites/{sid}/{suffix}"
            data = _call_list(client, path, style="offset", label=f"{name}:{label}", errors=errors)
            rel = f"sites/{safe}/{label}.json"
            _write_json(output / rel, data)
            site_summary["files"][label] = rel
            site_summary["counts"][label] = len(data)
        site_summaries.append(site_summary)

    _write_json(output / "network_site_summaries.json", site_summaries)
    summary["files"]["site_summaries"] = "network_site_summaries.json"
    summary["siteSummaries"] = site_summaries
    return summary


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect UniFi Site Manager and Network Application data.")
    parser.add_argument("--mode", choices=["auto", "site-manager", "network", "both"], default=os.getenv("UNIFI_COLLECTION_MODE", "auto"))
    parser.add_argument("--site-id", default=os.getenv("UNIFI_SITE_ID", ""))
    parser.add_argument("--output-dir", default=str(ROOT / "unifi" / "backups" / "latest"))
    args = parser.parse_args(argv)

    load_env()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    mode = args.mode
    if mode == "auto":
        has_network = bool((os.getenv("UNIFI_NETWORK_API_KEY") or os.getenv("UNIFI_API_KEY")) and (os.getenv("UNIFI_NETWORK_BASE_URL") or os.getenv("UNIFI_BASE_URL")))
        has_site_manager = bool(os.getenv("UNIFI_SITE_MANAGER_API_KEY") or os.getenv("UNIFI_API_KEY"))
        if has_network and has_site_manager:
            mode = "both"
        elif has_network:
            mode = "network"
        elif has_site_manager:
            mode = "site-manager"
        else:
            print("Missing UniFi API configuration.", file=sys.stderr)
            print("Set UNIFI_NETWORK_BASE_URL + UNIFI_NETWORK_API_KEY, or UNIFI_SITE_MANAGER_API_KEY.", file=sys.stderr)
            return 1

    metadata: Dict[str, Any] = {
        "collectedAt": datetime.now().isoformat(timespec="seconds"),
        "requestedMode": args.mode,
        "effectiveMode": mode,
        "sourceNotes": SOURCE_NOTES,
        "siteIdFilter": args.site_id or None,
    }
    summary: Dict[str, Any] = {"metadata": metadata}
    if mode in {"site-manager", "both"}:
        summary["siteManager"] = collect_site_manager(output)
    if mode in {"network", "both"}:
        summary["networkApplication"] = collect_network_application(output, args.site_id)

    _write_json(output / "collection_summary.json", summary)
    print(f"Collected UniFi data into {output}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

