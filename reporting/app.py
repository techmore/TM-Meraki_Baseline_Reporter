#!/usr/bin/env python3
import argparse
import json
import logging
import math
import os
import re
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .common import (
    BACKUPS_DIR,
    REPORT_VERSION,
    _format_usage_kb,
    _he,
    _hardware_consistency_note,
    _is_sfp_like_port,
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
    _build_addressing_dhcp_section,
    _build_appliance_policy_section,
    _build_budget_forecast_section,
    _build_config_coverage_section,
    _build_ap_spectrum_report,
    _build_switch_detail_section,
    _build_wan_capacity_section,
    _is_low_speed_link,
    _model_cell,
)
from .html_shell import build_html, write_pdf

log = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXED_NOW_ENV = "MERAKI_REPORT_FIXED_NOW"
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
HARDWARE_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reference",
    "meraki_hardware_catalog.json",
)
PRICING_REFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reference",
    "pricing_reference.json",
)
UPS_REFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reference",
    "ups_runtime_reference.json",
)
WIRELESS_DESIGN_REFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reference",
    "wireless_design_reference.json",
)
UPS_LOAD_BUFFER_RATIO = 0.10


def _report_slug(name: str) -> str:
    slug = re.sub(r"[^\w]+", "_", name or "Site").strip("_")
    return slug or "Site"


def _dated_report_name(org_name: str, label: str, run_ts: datetime, ext: str) -> str:
    date_stamp = run_ts.strftime("%Y-%m-%d")
    return f"{_report_slug(org_name)}_{label}_Report_{date_stamp}.{ext}"


def _current_run_ts() -> datetime:
    fixed_now = os.getenv(FIXED_NOW_ENV)
    if fixed_now:
        try:
            return datetime.fromisoformat(fixed_now.replace("Z", "+00:00"))
        except ValueError:
            log.warning("Ignoring invalid %s value: %s", FIXED_NOW_ENV, fixed_now)
    return datetime.now()


def _validate_fixed_now(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("must be an ISO timestamp, e.g. 2026-05-02T21:30:00")
    return value


def _load_hardware_catalog(org_dir: str) -> Dict[str, Any]:
    return (
        load_json(os.path.join(org_dir, "meraki_hardware_catalog.json"))
        or load_json(HARDWARE_CATALOG_PATH)
        or {}
    )


def _load_pricing_payload(org_dir: str) -> Dict[str, Any]:
    return (
        load_json(os.path.join(org_dir, "pricing.json"))
        or load_json(os.path.join(BASE_DIR, "pricing.json"))
        or load_json(PRICING_REFERENCE_PATH)
        or {}
    )


def _load_ups_payload(org_dir: str) -> Dict[str, Any]:
    return (
        load_json(os.path.join(org_dir, "ups_runtime_reference.json"))
        or load_json(os.path.join(BASE_DIR, "ups_runtime_reference.json"))
        or load_json(UPS_REFERENCE_PATH)
        or {}
    )


def _load_wireless_design_reference(org_dir: str) -> Dict[str, Any]:
    return (
        load_json(os.path.join(org_dir, "wireless_design_reference.json"))
        or load_json(WIRELESS_DESIGN_REFERENCE_PATH)
        or {}
    )


def _format_money(value: int | float | None) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "Pricing needed"
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _format_runtime_minutes(minutes: float | None) -> str:
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool):
        return "Over UPS rating"
    if minutes < 60:
        return f"{minutes:.0f} min" if minutes >= 10 else f"{minutes:.1f} min"
    hours = int(minutes // 60)
    mins = int(round(minutes % 60))
    if mins == 60:
        hours += 1
        mins = 0
    return f"{hours}h {mins:02d}m"


def _interpolate_runtime_minutes(points: Any, watts: float) -> float | None:
    if not isinstance(points, list) or not isinstance(watts, (int, float)) or watts <= 0:
        return None
    cleaned = sorted(
        (
            (float(p.get("watts")), float(p.get("minutes")))
            for p in points
            if isinstance(p, dict)
            and isinstance(p.get("watts"), (int, float))
            and isinstance(p.get("minutes"), (int, float))
            and p.get("watts") > 0
            and p.get("minutes") > 0
        ),
        key=lambda pair: pair[0],
    )
    if not cleaned:
        return None
    if watts <= cleaned[0][0]:
        return cleaned[0][1]
    if watts > cleaned[-1][0]:
        return None
    for (w1, m1), (w2, m2) in zip(cleaned, cleaned[1:]):
        if w1 <= watts <= w2:
            if watts == w1:
                return m1
            if watts == w2:
                return m2
            # Runtime curves are nonlinear. Log interpolation tracks UPS runtime charts
            # better than a straight-line fit between sparse vendor chart points.
            ratio = (math.log(watts) - math.log(w1)) / (math.log(w2) - math.log(w1))
            return math.exp(math.log(m1) + ratio * (math.log(m2) - math.log(m1)))
    return None


def _catalog_poe_budget(hardware_catalog: Dict[str, Any], model: str) -> int | float | None:
    models = (
        hardware_catalog.get("models")
        if isinstance(hardware_catalog, dict) and isinstance(hardware_catalog.get("models"), dict)
        else {}
    )
    ref = models.get(model) or {}
    budget = ref.get("poeBudgetWatts") if isinstance(ref, dict) else None
    return budget if isinstance(budget, (int, float)) else None


def _estimated_switch_base_watts(model: str, ups_assumptions: Dict[str, Any]) -> tuple[float, str]:
    prefixes = (
        ups_assumptions.get("model_prefixes")
        if isinstance(ups_assumptions, dict) and isinstance(ups_assumptions.get("model_prefixes"), dict)
        else {}
    )
    for prefix, watts in sorted(prefixes.items(), key=lambda item: len(str(item[0])), reverse=True):
        if model.startswith(str(prefix)) and isinstance(watts, (int, float)):
            return float(watts), f"model prefix {prefix}"

    fallback = (
        ups_assumptions.get("fallback_by_port_count")
        if isinstance(ups_assumptions, dict) and isinstance(ups_assumptions.get("fallback_by_port_count"), dict)
        else {}
    )
    for port_count in ("48", "24", "8"):
        if re.search(rf"(?:^|[-_]){port_count}(?:[A-Z-]|$)", model):
            value = fallback.get(port_count)
            if isinstance(value, (int, float)):
                return float(value), f"port-count fallback {port_count}"
    value = fallback.get("default")
    if isinstance(value, (int, float)):
        return float(value), "default fallback"
    return 75.0, "built-in default fallback"


def _ups_runtime(product: Dict[str, Any], watts: float) -> float | None:
    max_watts = product.get("max_watts") if isinstance(product, dict) else None
    if isinstance(max_watts, (int, float)) and watts > float(max_watts):
        return None
    return _interpolate_runtime_minutes(product.get("runtime_points_minutes"), watts)


def _smx_runtime_config(smx_ref: Dict[str, Any], config: Dict[str, Any], watts: float) -> float | None:
    max_watts = smx_ref.get("max_watts") if isinstance(smx_ref, dict) else None
    if isinstance(max_watts, (int, float)) and watts > float(max_watts):
        return None
    return _interpolate_runtime_minutes(config.get("runtime_points_minutes"), watts)


def _smx_stack_cost(smx_ref: Dict[str, Any], external_count: int) -> float | None:
    unit = smx_ref.get("unit_cost") if isinstance(smx_ref, dict) else None
    ext = smx_ref.get("external_battery_unit_cost") if isinstance(smx_ref, dict) else None
    if not isinstance(unit, (int, float)) or not isinstance(ext, (int, float)):
        return None
    return float(unit) + (external_count * float(ext))


def _round_watts(value: float) -> float:
    return math.ceil(float(value) * 10) / 10


def _build_ups_power_plan(
    org_name: str,
    switch_devices: List[Dict[str, Any]],
    poe_by_serial: Dict[str, Dict[str, Any]],
    ups_payload: Dict[str, Any],
    hardware_catalog: Dict[str, Any],
    run_ts: datetime,
) -> Dict[str, Any]:
    ups_meta = ups_payload.get("meta") if isinstance(ups_payload, dict) else {}
    ups_products = ups_payload.get("products") if isinstance(ups_payload, dict) else {}
    ups_assumptions = (
        ups_payload.get("switch_load_assumptions") if isinstance(ups_payload, dict) else {}
    )
    target_hours = (
        float(ups_meta.get("target_runtime_hours"))
        if isinstance(ups_meta, dict) and isinstance(ups_meta.get("target_runtime_hours"), (int, float))
        else 10.0
    )
    target_minutes = target_hours * 60
    bx_ref = ups_products.get("BX1500M") if isinstance(ups_products, dict) else {}
    smx_ref = ups_products.get("SMX2200RMLV2U") if isinstance(ups_products, dict) else {}
    runtime_configs = (
        smx_ref.get("runtime_configurations")
        if isinstance(smx_ref, dict) and isinstance(smx_ref.get("runtime_configurations"), list)
        else []
    )
    base_config = next(
        (
            config for config in runtime_configs
            if isinstance(config, dict) and int(config.get("external_battery_count") or 0) == 0
        ),
        {},
    )

    switches: List[Dict[str, Any]] = []
    for sw in sorted(
        switch_devices,
        key=lambda d: (
            str((d.get("network") or {}).get("name") or ""),
            str(d.get("name") or d.get("model") or d.get("serial") or ""),
        ),
    ):
        serial = str(sw.get("serial") or "")
        model = str(sw.get("model") or "")
        label = str(sw.get("name") or model or serial or "Unknown switch")
        network = sw.get("network") if isinstance(sw.get("network"), dict) else {}
        poe_data = poe_by_serial.get(serial, {}) if isinstance(poe_by_serial, dict) else {}
        observed_poe = float(poe_data.get("avgWatts", 0) or 0)
        chassis_watts, chassis_source = _estimated_switch_base_watts(model, ups_assumptions)
        modeled_load = observed_poe + chassis_watts
        buffer_watts = modeled_load * UPS_LOAD_BUFFER_RATIO
        sizing_load = modeled_load + buffer_watts

        bx_runtime = _ups_runtime(bx_ref, sizing_load) if isinstance(bx_ref, dict) else None
        smx_base_runtime = (
            _smx_runtime_config(smx_ref, base_config, sizing_load)
            if isinstance(smx_ref, dict) and base_config
            else None
        )
        target_config: Dict[str, Any] | None = None
        target_runtime: float | None = None
        for config in sorted(
            [c for c in runtime_configs if isinstance(c, dict)],
            key=lambda c: int(c.get("external_battery_count") or 0),
        ):
            runtime = _smx_runtime_config(smx_ref, config, sizing_load)
            if runtime is not None and runtime >= target_minutes:
                target_config = config
                target_runtime = runtime
                break
        target_external_count = (
            int(target_config.get("external_battery_count") or 0)
            if target_config is not None
            else None
        )
        target_label = (
            str(target_config.get("label") or f"1 UPS + {target_external_count} external battery module(s)")
            if target_config is not None
            else "No listed stack reaches target"
        )
        target_cost = (
            _smx_stack_cost(smx_ref, target_external_count)
            if isinstance(target_external_count, int)
            else None
        )
        switches.append(
            {
                "siteName": network.get("name") or "Unassigned",
                "networkId": network.get("id") or sw.get("networkId"),
                "switchName": label,
                "serial": serial,
                "model": model or "Unknown",
                "status": sw.get("status") or "unknown",
                "observedPoeAvgWatts": _round_watts(observed_poe),
                "observedPoeSource": "poe_power_summary.json avgWatts" if poe_data else "not observed; treated as 0 W",
                "chassisEstimateWatts": _round_watts(chassis_watts),
                "chassisEstimateSource": chassis_source,
                "knownPoeBudgetWatts": _catalog_poe_budget(hardware_catalog, model),
                "baseModeledLoadWatts": _round_watts(modeled_load),
                "bufferRatio": UPS_LOAD_BUFFER_RATIO,
                "bufferWatts": _round_watts(buffer_watts),
                "sizingLoadWatts": _round_watts(sizing_load),
                "runtimeEstimates": {
                    "BX1500M": {
                        "runtimeMinutes": round(bx_runtime, 1) if bx_runtime is not None else None,
                        "runtimeLabel": _format_runtime_minutes(bx_runtime),
                    },
                    "SMX2200RMLV2UBase": {
                        "runtimeMinutes": round(smx_base_runtime, 1) if smx_base_runtime is not None else None,
                        "runtimeLabel": _format_runtime_minutes(smx_base_runtime),
                    },
                    "SMX2200RMLV2UTargetStack": {
                        "targetRuntimeHours": target_hours,
                        "label": target_label,
                        "externalBatteryCount": target_external_count,
                        "runtimeMinutes": round(target_runtime, 1) if target_runtime is not None else None,
                        "runtimeLabel": _format_runtime_minutes(target_runtime),
                        "estimatedCost": round(target_cost, 2) if isinstance(target_cost, (int, float)) else None,
                        "estimatedCostLabel": _format_money(target_cost),
                    },
                },
            }
        )

    site_summary: Dict[str, Dict[str, Any]] = {}
    for item in switches:
        site = site_summary.setdefault(
            item["siteName"],
            {"switchCount": 0, "totalSizingLoadWatts": 0.0, "maxSizingLoadWatts": 0.0},
        )
        site["switchCount"] += 1
        site["totalSizingLoadWatts"] += float(item["sizingLoadWatts"])
        site["maxSizingLoadWatts"] = max(site["maxSizingLoadWatts"], float(item["sizingLoadWatts"]))
    for site in site_summary.values():
        site["totalSizingLoadWatts"] = _round_watts(site["totalSizingLoadWatts"])
        site["maxSizingLoadWatts"] = _round_watts(site["maxSizingLoadWatts"])

    sizing_loads = [float(item["sizingLoadWatts"]) for item in switches]
    base_loads = [float(item["baseModeledLoadWatts"]) for item in switches]
    return {
        "schemaVersion": 1,
        "orgName": org_name,
        "generatedAt": run_ts.isoformat(),
        "sourceFiles": [
            "devices_availabilities.json",
            "inventory_devices.json",
            "devices_statuses.json",
            "poe_power_summary.json",
            "reporting/reference/ups_runtime_reference.json",
            "reporting/reference/meraki_hardware_catalog.json",
        ],
        "planningAssumptions": {
            "loadBufferRatio": UPS_LOAD_BUFFER_RATIO,
            "loadBufferPercent": int(UPS_LOAD_BUFFER_RATIO * 100),
            "modeledLoadFormula": "observed Meraki PoE average + switch chassis/base estimate",
            "sizingLoadFormula": "modeled load * 1.10",
            "targetRuntimeHours": target_hours,
            "runtimeInterpolation": "log interpolation across maintained UPS runtime chart points",
        },
        "summary": {
            "switchCount": len(switches),
            "averageBaseModeledLoadWatts": _round_watts(sum(base_loads) / len(base_loads)) if base_loads else 0,
            "averageSizingLoadWatts": _round_watts(sum(sizing_loads) / len(sizing_loads)) if sizing_loads else 0,
            "maxSizingLoadWatts": _round_watts(max(sizing_loads)) if sizing_loads else 0,
            "totalSizingLoadWatts": _round_watts(sum(sizing_loads)) if sizing_loads else 0,
        },
        "sites": dict(sorted(site_summary.items())),
        "switches": switches,
    }


def _load_ups_power_plan_from_org(org_dir: str, org_name: str, run_ts: datetime) -> Dict[str, Any]:
    devices_avail = load_json(os.path.join(org_dir, "devices_availabilities.json")) or []
    inventory_devices = load_json(os.path.join(org_dir, "inventory_devices.json")) or []
    devices_statuses_raw = load_json(os.path.join(org_dir, "devices_statuses.json")) or []
    networks = load_json(os.path.join(org_dir, "networks.json")) or []
    poe_summary = load_json(os.path.join(org_dir, "poe_power_summary.json")) or {}
    hardware_catalog = _load_hardware_catalog(org_dir)
    ups_payload = _load_ups_payload(org_dir)
    network_names = {
        n.get("id"): n.get("name", n.get("id", ""))
        for n in networks
        if isinstance(n, dict) and n.get("id")
    }

    metadata_by_serial: Dict[str, Dict[str, Any]] = {}
    for source in (inventory_devices, devices_statuses_raw):
        if not isinstance(source, list):
            continue
        for entry in source:
            if not isinstance(entry, dict) or not entry.get("serial"):
                continue
            serial = entry["serial"]
            merged = metadata_by_serial.setdefault(serial, {})
            for key in ("name", "model", "sku", "mac", "productType", "networkId", "tags", "lanIp"):
                if not merged.get(key) and entry.get(key):
                    merged[key] = entry[key]

    enriched: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for device in devices_avail if isinstance(devices_avail, list) else []:
        if not isinstance(device, dict):
            continue
        serial = device.get("serial")
        if serial:
            seen.add(serial)
        merged = dict(device)
        for key, value in metadata_by_serial.get(serial, {}).items():
            if not merged.get(key) and value:
                merged[key] = value
        net_id = merged.get("networkId") or (merged.get("network") or {}).get("id")
        if net_id and not merged.get("network"):
            merged["network"] = {"id": net_id, "name": network_names.get(net_id, net_id)}
        elif net_id and isinstance(merged.get("network"), dict) and not merged["network"].get("name"):
            merged["network"]["name"] = network_names.get(net_id, net_id)
        enriched.append(merged)

    for serial, meta in sorted(metadata_by_serial.items()):
        if serial in seen:
            continue
        device = dict(meta)
        device["serial"] = serial
        device.setdefault("status", "unknown")
        net_id = device.get("networkId")
        if net_id and not device.get("network"):
            device["network"] = {"id": net_id, "name": network_names.get(net_id, net_id)}
        elif net_id and isinstance(device.get("network"), dict) and not device["network"].get("name"):
            device["network"]["name"] = network_names.get(net_id, net_id)
        enriched.append(device)

    switch_devices = [
        d for d in enriched
        if isinstance(d, dict) and d.get("productType") == "switch"
    ]
    poe_switches = (
        poe_summary.get("switch_poe_totals", [])
        if isinstance(poe_summary, dict)
        else []
    )
    poe_by_serial = {s.get("serial", ""): s for s in poe_switches if isinstance(s, dict)}
    return _build_ups_power_plan(
        org_name,
        switch_devices,
        poe_by_serial,
        ups_payload,
        hardware_catalog,
        run_ts,
    )


def _read_org_name(org_dir: str) -> str:
    name_file = os.path.join(org_dir, "org_name.txt")
    if os.path.exists(name_file):
        with open(name_file, "r", encoding="utf-8") as nf:
            return nf.read().strip()

    org_name = os.path.basename(org_dir)
    rec_path = os.path.join(org_dir, "recommendations.md")
    if os.path.exists(rec_path):
        with open(rec_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            m = re.match(r"# Meraki Recommendations: (.+)$", first_line)
            if m:
                org_name = m.group(1)
    return org_name


def _write_text_aliases(html: str, paths: tuple[str | None, ...]) -> None:
    for path in paths:
        if not path:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


def _write_json_aliases(payload: Dict[str, Any], paths: tuple[str | None, ...]) -> None:
    for path in paths:
        if not path:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")


def _copy_existing(src: str, destinations: tuple[str | None, ...]) -> None:
    for dst in destinations:
        if not dst or os.path.abspath(src) == os.path.abspath(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def _cleanup_paths(paths: tuple[str, ...]) -> None:
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            log.warning("Unable to remove generated HTML artifact: %s", path)


def _report_run_output_dir(reports_dir: str, org_name: str, run_ts: datetime) -> str:
    return os.path.join(
        reports_dir,
        _report_slug(org_name),
        run_ts.strftime("%Y-%m-%d_%H%M"),
    )


def _report_latest_output_dir(reports_dir: str, org_name: str) -> str:
    return os.path.join(reports_dir, "latest", _report_slug(org_name))


def generate_org_reports(
    source_dir: str,
    org_name: str,
    output_dir: str | None = None,
    *,
    latest_dir: str | None = None,
    keep_html: bool = True,
    run_ts: datetime | None = None,
) -> int:
    _run_ts = run_ts or _current_run_ts()
    output_dir = output_dir or source_dir
    os.makedirs(output_dir, exist_ok=True)
    if latest_dir:
        os.makedirs(latest_dir, exist_ok=True)

    log.info("Generating report for: %s", org_name)
    _slug = _report_slug(org_name)
    _stamp = _run_ts.strftime("%Y-%m-%d_%H%M")
    ups_power_plan = _load_ups_power_plan_from_org(source_dir, org_name, _run_ts)
    ups_plan_named_json = os.path.join(output_dir, _dated_report_name(org_name, "UPS_Switch_Power_Plan", _run_ts, "json"))
    ups_plan_json = os.path.join(output_dir, "ups_switch_power_plan.json")
    latest_ups_plan_named_json = (
        os.path.join(latest_dir, _dated_report_name(org_name, "UPS_Switch_Power_Plan", _run_ts, "json"))
        if latest_dir
        else None
    )
    latest_ups_plan_json = os.path.join(latest_dir, "ups_switch_power_plan.json") if latest_dir else None
    _write_json_aliases(
        ups_power_plan,
        (ups_plan_named_json, ups_plan_json, latest_ups_plan_named_json, latest_ups_plan_json),
    )

    body = build_org_report(source_dir, org_name)
    html = build_html(f"{org_name} — Network Health Report", body)
    html_path = os.path.join(output_dir, f"{_slug}_{_stamp}_report.html")
    pdf_path = os.path.join(output_dir, f"{_slug}_{_stamp}_report.pdf")
    named_html_alias = os.path.join(output_dir, _dated_report_name(org_name, "Complete", _run_ts, "html"))
    named_pdf_alias = os.path.join(output_dir, _dated_report_name(org_name, "Complete", _run_ts, "pdf"))
    html_alias = os.path.join(output_dir, "report.html")
    pdf_alias = os.path.join(output_dir, "report.pdf")
    if latest_dir:
        html_path = named_html_alias
        pdf_path = named_pdf_alias
        html_alias = None
        pdf_alias = None
    latest_html_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Complete", _run_ts, "html")) if latest_dir else None
    latest_pdf_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Complete", _run_ts, "pdf")) if latest_dir else None
    latest_html_compat = os.path.join(latest_dir, "report.html") if latest_dir else None
    latest_pdf_compat = os.path.join(latest_dir, "report.pdf") if latest_dir else None

    _write_text_aliases(html, (html_path, named_html_alias, html_alias))
    if latest_dir:
        _write_text_aliases(html, (latest_html_alias, latest_html_compat))
    pdf_ok = write_pdf(html_path, pdf_path)
    if pdf_ok:
        _copy_existing(pdf_path, (named_pdf_alias, pdf_alias))
        if latest_dir:
            _copy_existing(pdf_path, (latest_pdf_alias, latest_pdf_compat))
        log.info("PDF → %s", named_pdf_alias)
    else:
        log.info("HTML → %s  (no PDF tool found)", html_path)
    if not keep_html and pdf_ok:
        html_targets = [html_path, named_html_alias, html_alias]
        if latest_dir:
            html_targets.extend([latest_html_alias, latest_html_compat])
        _cleanup_paths(tuple(path for path in html_targets if path))

    exec_body = build_org_report(source_dir, org_name, report_kind="exec")
    exec_html = build_html(f"{org_name} — Executive Summary", exec_body)
    exec_html_path = os.path.join(output_dir, f"{_slug}_{_stamp}_exec_summary_report.html")
    exec_pdf_path = os.path.join(output_dir, f"{_slug}_{_stamp}_exec_summary_report.pdf")
    exec_named_html_alias = os.path.join(output_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "html"))
    exec_named_pdf_alias = os.path.join(output_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "pdf"))
    exec_html_alias = os.path.join(output_dir, "report_exec_summary.html")
    exec_pdf_alias = os.path.join(output_dir, "report_exec_summary.pdf")
    if latest_dir:
        exec_html_path = exec_named_html_alias
        exec_pdf_path = exec_named_pdf_alias
        exec_html_alias = None
        exec_pdf_alias = None
    latest_exec_html_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "html")) if latest_dir else None
    latest_exec_pdf_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Executive_Summary", _run_ts, "pdf")) if latest_dir else None
    latest_exec_html_compat = os.path.join(latest_dir, "report_exec_summary.html") if latest_dir else None
    latest_exec_pdf_compat = os.path.join(latest_dir, "report_exec_summary.pdf") if latest_dir else None
    _write_text_aliases(exec_html, (exec_html_path, exec_named_html_alias, exec_html_alias))
    if latest_dir:
        _write_text_aliases(exec_html, (latest_exec_html_alias, latest_exec_html_compat))
    exec_pdf_ok = write_pdf(exec_html_path, exec_pdf_path)
    if exec_pdf_ok:
        _copy_existing(exec_pdf_path, (exec_named_pdf_alias, exec_pdf_alias))
        if latest_dir:
            _copy_existing(exec_pdf_path, (latest_exec_pdf_alias, latest_exec_pdf_compat))
        log.info("Exec Summary PDF → %s", exec_named_pdf_alias)
    else:
        log.info("Exec Summary HTML → %s  (no PDF tool found)", exec_html_path)
    if not keep_html and exec_pdf_ok:
        html_targets = [exec_html_path, exec_named_html_alias, exec_html_alias]
        if latest_dir:
            html_targets.extend([latest_exec_html_alias, latest_exec_html_compat])
        _cleanup_paths(tuple(path for path in html_targets if path))

    backup_body = build_org_report(source_dir, org_name, report_kind="backup")
    backup_html = build_html(f"{org_name} — Backup Settings Report", backup_body)
    backup_html_path = os.path.join(output_dir, f"{_slug}_{_stamp}_backup_settings_report.html")
    backup_pdf_path = os.path.join(output_dir, f"{_slug}_{_stamp}_backup_settings_report.pdf")
    backup_named_html_alias = os.path.join(output_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "html"))
    backup_named_pdf_alias = os.path.join(output_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "pdf"))
    backup_html_alias = os.path.join(output_dir, "report_backup_settings.html")
    backup_pdf_alias = os.path.join(output_dir, "report_backup_settings.pdf")
    if latest_dir:
        backup_html_path = backup_named_html_alias
        backup_pdf_path = backup_named_pdf_alias
        backup_html_alias = None
        backup_pdf_alias = None
    latest_backup_html_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "html")) if latest_dir else None
    latest_backup_pdf_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Backup_Settings", _run_ts, "pdf")) if latest_dir else None
    latest_backup_html_compat = os.path.join(latest_dir, "report_backup_settings.html") if latest_dir else None
    latest_backup_pdf_compat = os.path.join(latest_dir, "report_backup_settings.pdf") if latest_dir else None
    _write_text_aliases(backup_html, (backup_html_path, backup_named_html_alias, backup_html_alias))
    if latest_dir:
        _write_text_aliases(backup_html, (latest_backup_html_alias, latest_backup_html_compat))
    backup_pdf_ok = write_pdf(backup_html_path, backup_pdf_path)
    if backup_pdf_ok:
        _copy_existing(backup_pdf_path, (backup_named_pdf_alias, backup_pdf_alias))
        if latest_dir:
            _copy_existing(backup_pdf_path, (latest_backup_pdf_alias, latest_backup_pdf_compat))
        log.info("Backup Settings PDF → %s", backup_named_pdf_alias)
    else:
        log.info("Backup Settings HTML → %s  (no PDF tool found)", backup_html_path)
    if not keep_html and backup_pdf_ok:
        html_targets = [backup_html_path, backup_named_html_alias, backup_html_alias]
        if latest_dir:
            html_targets.extend([latest_backup_html_alias, latest_backup_html_compat])
        _cleanup_paths(tuple(path for path in html_targets if path))

    battery_body = build_org_report(source_dir, org_name, report_kind="battery_backup")
    battery_html = build_html(f"{org_name} — Battery Backup Pricing & Runtime Calculation", battery_body)
    battery_html_path = os.path.join(output_dir, f"{_slug}_{_stamp}_battery_backup_report.html")
    battery_pdf_path = os.path.join(output_dir, f"{_slug}_{_stamp}_battery_backup_report.pdf")
    battery_named_html_alias = os.path.join(output_dir, _dated_report_name(org_name, "Battery_Backup_Pricing_Calculation", _run_ts, "html"))
    battery_named_pdf_alias = os.path.join(output_dir, _dated_report_name(org_name, "Battery_Backup_Pricing_Calculation", _run_ts, "pdf"))
    battery_html_alias = os.path.join(output_dir, "report_battery_backup.html")
    battery_pdf_alias = os.path.join(output_dir, "report_battery_backup.pdf")
    if latest_dir:
        battery_html_path = battery_named_html_alias
        battery_pdf_path = battery_named_pdf_alias
        battery_html_alias = None
        battery_pdf_alias = None
    latest_battery_html_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Battery_Backup_Pricing_Calculation", _run_ts, "html")) if latest_dir else None
    latest_battery_pdf_alias = os.path.join(latest_dir, _dated_report_name(org_name, "Battery_Backup_Pricing_Calculation", _run_ts, "pdf")) if latest_dir else None
    latest_battery_html_compat = os.path.join(latest_dir, "report_battery_backup.html") if latest_dir else None
    latest_battery_pdf_compat = os.path.join(latest_dir, "report_battery_backup.pdf") if latest_dir else None
    _write_text_aliases(battery_html, (battery_html_path, battery_named_html_alias, battery_html_alias))
    if latest_dir:
        _write_text_aliases(battery_html, (latest_battery_html_alias, latest_battery_html_compat))
    battery_pdf_ok = write_pdf(battery_html_path, battery_pdf_path)
    if battery_pdf_ok:
        _copy_existing(battery_pdf_path, (battery_named_pdf_alias, battery_pdf_alias))
        if latest_dir:
            _copy_existing(battery_pdf_path, (latest_battery_pdf_alias, latest_battery_pdf_compat))
        log.info("Battery Backup PDF → %s", battery_named_pdf_alias)
    else:
        log.info("Battery Backup HTML → %s  (no PDF tool found)", battery_html_path)
    if not keep_html and battery_pdf_ok:
        html_targets = [battery_html_path, battery_named_html_alias, battery_html_alias]
        if latest_dir:
            html_targets.extend([latest_battery_html_alias, latest_battery_html_compat])
        _cleanup_paths(tuple(path for path in html_targets if path))

    ap_spectrum_body = build_org_report(source_dir, org_name, report_kind="ap_spectrum")
    ap_spectrum_html = build_html(f"{org_name} — AP Spectrum & Interference Report", ap_spectrum_body)
    ap_spectrum_html_path = os.path.join(output_dir, f"{_slug}_{_stamp}_ap_spectrum_report.html")
    ap_spectrum_pdf_path = os.path.join(output_dir, f"{_slug}_{_stamp}_ap_spectrum_report.pdf")
    ap_spectrum_named_html_alias = os.path.join(output_dir, _dated_report_name(org_name, "AP_Spectrum", _run_ts, "html"))
    ap_spectrum_named_pdf_alias = os.path.join(output_dir, _dated_report_name(org_name, "AP_Spectrum", _run_ts, "pdf"))
    ap_spectrum_html_alias = os.path.join(output_dir, "report_ap_spectrum.html")
    ap_spectrum_pdf_alias = os.path.join(output_dir, "report_ap_spectrum.pdf")
    if latest_dir:
        ap_spectrum_html_path = ap_spectrum_named_html_alias
        ap_spectrum_pdf_path = ap_spectrum_named_pdf_alias
        ap_spectrum_html_alias = None
        ap_spectrum_pdf_alias = None
    latest_ap_spectrum_html_alias = os.path.join(latest_dir, _dated_report_name(org_name, "AP_Spectrum", _run_ts, "html")) if latest_dir else None
    latest_ap_spectrum_pdf_alias = os.path.join(latest_dir, _dated_report_name(org_name, "AP_Spectrum", _run_ts, "pdf")) if latest_dir else None
    latest_ap_spectrum_html_compat = os.path.join(latest_dir, "report_ap_spectrum.html") if latest_dir else None
    latest_ap_spectrum_pdf_compat = os.path.join(latest_dir, "report_ap_spectrum.pdf") if latest_dir else None
    _write_text_aliases(ap_spectrum_html, (ap_spectrum_html_path, ap_spectrum_named_html_alias, ap_spectrum_html_alias))
    if latest_dir:
        _write_text_aliases(ap_spectrum_html, (latest_ap_spectrum_html_alias, latest_ap_spectrum_html_compat))
    ap_spectrum_pdf_ok = write_pdf(ap_spectrum_html_path, ap_spectrum_pdf_path)
    if ap_spectrum_pdf_ok:
        _copy_existing(ap_spectrum_pdf_path, (ap_spectrum_named_pdf_alias, ap_spectrum_pdf_alias))
        if latest_dir:
            _copy_existing(ap_spectrum_pdf_path, (latest_ap_spectrum_pdf_alias, latest_ap_spectrum_pdf_compat))
        log.info("AP Spectrum PDF → %s", ap_spectrum_named_pdf_alias)
    else:
        log.info("AP Spectrum HTML → %s  (no PDF tool found)", ap_spectrum_html_path)
    if not keep_html and ap_spectrum_pdf_ok:
        html_targets = [ap_spectrum_html_path, ap_spectrum_named_html_alias, ap_spectrum_html_alias]
        if latest_dir:
            html_targets.extend([latest_ap_spectrum_html_alias, latest_ap_spectrum_html_compat])
        _cleanup_paths(tuple(path for path in html_targets if path))

    return 1

def build_org_report(
    org_dir: str,
    org_name: str,
    exec_purpose: str = "",
    report_kind: str = "full",
) -> str:
    _now = _current_run_ts()
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
    # network_clients.json is {net_id: [client, …]} from GET /networks/{id}/clients.
    # Older backups used wireless_clients.json from a now-unreliable wireless-only path.
    def _flatten_client_records(raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, dict):
            return [
                cl for clients in raw.values()
                if isinstance(clients, list)
                for cl in clients
                if isinstance(cl, dict)
            ]
        if isinstance(raw, list):
            return [cl for cl in raw if isinstance(cl, dict)]
        return []

    network_clients_raw = load_json(os.path.join(org_dir, "network_clients.json")) or {}
    _wc_raw = load_json(os.path.join(org_dir, "wireless_clients.json")) or {}
    network_clients = _flatten_client_records(network_clients_raw)
    wireless_clients = _flatten_client_records(_wc_raw)
    client_records = network_clients or wireless_clients
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
    rf_profile_assignments = load_json(os.path.join(org_dir, "wireless_rf_profile_assignments.json")) or {}
    inventory_devices = load_json(os.path.join(org_dir, "inventory_devices.json")) or []
    firmware_upgrades = load_json(os.path.join(org_dir, "firmware_upgrades.json")) or []
    wireless_settings = load_json(os.path.join(org_dir, "wireless_settings.json")) or {}
    wireless_ssids = load_json(os.path.join(org_dir, "wireless_ssids.json")) or {}
    alerts_history = load_json(os.path.join(org_dir, "alerts_history.json")) or {}
    wireless_event_log = load_json(os.path.join(org_dir, "wireless_event_log.json")) or {}
    wireless_mesh_statuses = load_json(os.path.join(org_dir, "wireless_mesh_statuses.json")) or {}
    appliance_vlans = load_json(os.path.join(org_dir, "appliance_vlans.json")) or {}
    appliance_dhcp_subnets = load_json(os.path.join(org_dir, "appliance_dhcp_subnets.json")) or {}
    appliance_policy_backup = load_json(os.path.join(org_dir, "appliance_policy_backup.json")) or {}
    pricing_payload = _load_pricing_payload(org_dir)
    hardware_catalog = _load_hardware_catalog(org_dir)
    ups_payload = _load_ups_payload(org_dir)
    wireless_design_reference = _load_wireless_design_reference(org_dir)

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

    def _merge_device_metadata() -> List[Dict]:
        """Availability records are status-first; enrich them with inventory labels/models."""
        metadata_by_serial: Dict[str, Dict] = {}
        for source in (inventory_devices, devices_statuses_raw):
            if not isinstance(source, list):
                continue
            for entry in source:
                if not isinstance(entry, dict) or not entry.get("serial"):
                    continue
                serial = entry["serial"]
                merged = metadata_by_serial.setdefault(serial, {})
                for key in (
                    "name",
                    "model",
                    "sku",
                    "mac",
                    "productType",
                    "networkId",
                    "tags",
                    "lanIp",
                ):
                    if not merged.get(key) and entry.get(key):
                        merged[key] = entry[key]

        enriched: List[Dict] = []
        seen: set[str] = set()
        for device in devices_avail if isinstance(devices_avail, list) else []:
            if not isinstance(device, dict):
                continue
            serial = device.get("serial")
            if serial:
                seen.add(serial)
            merged = dict(device)
            for key, value in metadata_by_serial.get(serial, {}).items():
                if not merged.get(key) and value:
                    merged[key] = value
            net_id = merged.get("networkId") or (merged.get("network") or {}).get("id")
            if net_id and not merged.get("network"):
                merged["network"] = {
                    "id": net_id,
                    "name": network_names.get(net_id, net_id),
                }
            elif net_id and isinstance(merged.get("network"), dict) and not merged["network"].get("name"):
                merged["network"]["name"] = network_names.get(net_id, net_id)
            enriched.append(merged)

        # Keep inventory-only devices visible instead of silently dropping them.
        for serial, meta in sorted(metadata_by_serial.items()):
            if serial in seen:
                continue
            device = dict(meta)
            device["serial"] = serial
            device.setdefault("status", "unknown")
            net_id = device.get("networkId")
            if net_id and not device.get("network"):
                device["network"] = {
                    "id": net_id,
                    "name": network_names.get(net_id, net_id),
                }
            elif net_id and isinstance(device.get("network"), dict) and not device["network"].get("name"):
                device["network"]["name"] = network_names.get(net_id, net_id)
            enriched.append(device)
        return enriched

    devices_avail = _merge_device_metadata()
    device_by_serial = {
        dev.get("serial"): dev
        for dev in devices_avail
        if isinstance(dev, dict) and dev.get("serial")
    }
    catalog_models = (
        hardware_catalog.get("models")
        if isinstance(hardware_catalog, dict) and isinstance(hardware_catalog.get("models"), dict)
        else {}
    )

    def _known_poe_budget(model: str) -> int | float | None:
        if not model:
            return None
        ref = catalog_models.get(model) or {}
        budget = ref.get("poeBudgetWatts")
        return budget if isinstance(budget, (int, float)) else None

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
    def _meaningful_port_errors(errors: list[str]) -> list[str]:
        benign_fragments = (
            "disconnected",
            "not connected",
            "no link",
            "link down",
            "down",
        )
        result = []
        for error in errors:
            text = str(error or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if any(fragment in lowered for fragment in benign_fragments):
                continue
            result.append(text)
        return result

    switch_port_issues = []
    if isinstance(switch_port_statuses, list):
        for port in switch_port_statuses[:100]:
            port_errors = port.get("errors") or []  # always a list
            if isinstance(port_errors, str):
                port_errors = [port_errors]
            port_errors = _meaningful_port_errors(port_errors)
            port_warnings = port.get("warnings") or []
            if isinstance(port_warnings, str):
                port_warnings = [port_warnings]
            speed_raw = port.get("speed") or ""
            is_uplink = bool(port.get("isUplink"))
            if any(
                [
                    bool(port_errors),
                    is_uplink and _is_low_speed_link(speed_raw),
                ]
            ):
                switch_serial = port.get("switchSerial", "Unknown")
                switch_device = device_by_serial.get(switch_serial) or {}
                switch_port_issues.append(
                    {
                        "switch": switch_serial,
                        "switch_name": switch_device.get("name") or switch_device.get("model") or switch_serial,
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

    switch_devices = [
        d for d in devices_avail
        if isinstance(d, dict) and d.get("productType") == "switch"
    ]
    switch_budget_known = sum(
        1 for d in switch_devices if _known_poe_budget(str(d.get("model") or "")) is not None
    )
    switch_budget_total = len(switch_devices)
    poe_budget_note = (
        f"The local hardware catalog contains PoE budget references for "
        f"{switch_budget_known} of {switch_budget_total} switch device(s) in this backup. "
        "Where a model is covered, the report shows measured draw against known hardware "
        "budget and calculates headroom. Models not yet in the catalog are left as unknown "
        "instead of estimated."
        if switch_budget_total
        else "No switch inventory was available for PoE budget coverage analysis."
    )

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
    def _iter_wan_uplinks(raw_uplinks: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not isinstance(raw_uplinks, list):
            return rows
        for item in raw_uplinks:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("uplinks"), list):
                for uplink in item["uplinks"]:
                    if isinstance(uplink, dict):
                        merged = dict(uplink)
                        merged.setdefault("serial", item.get("serial"))
                        merged.setdefault("model", item.get("model"))
                        merged.setdefault("networkId", item.get("networkId"))
                        rows.append(merged)
            else:
                rows.append(item)
        return rows

    _wan_uplinks = _iter_wan_uplinks(uplink_statuses)
    _wan_active = sum(
        1 for u in _wan_uplinks
        if isinstance(u, dict) and str(u.get("status", "")).lower() == "active"
    )
    _wan_ready = sum(
        1 for u in _wan_uplinks
        if isinstance(u, dict) and str(u.get("status", "")).lower() == "ready"
    )
    _wan_total = sum(
        1 for u in _wan_uplinks
        if isinstance(u, dict) and u.get("interface")
    )
    _wan_down = _wan_total - _wan_active - _wan_ready
    if _wan_total == 0:
        _wan_rating, _wan_stat, _wan_detail = "info", "No WAN data", "uplink status unavailable"
    elif _wan_down > 0:
        _wan_rating = "crit" if _wan_active == 0 else "warn"
        _wan_stat = f"{_wan_down} link{'s' if _wan_down != 1 else ''} down"
        _wan_detail = f"{_wan_active} active · {_wan_ready} ready of {_wan_total} uplinks"
    else:
        _wan_rating = "good"
        _wan_stat = f"{_wan_active} active"
        _wan_detail = (
            f"{_wan_ready} standby-ready · {_wan_total} total"
            if _wan_ready
            else f"{_wan_total} uplink{'s' if _wan_total != 1 else ''} healthy"
        )
    _wan_card = _hcard("WAN / Internet", _wan_rating, _wan_stat, _wan_detail)

    # Security
    def _check_status(check: Dict[str, Any]) -> str:
        return str(check.get("status") or "").strip().lower()

    _sec_fail  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and _check_status(c) == "fail")
    _sec_warn  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and _check_status(c) == "warning")
    _sec_pass  = sum(1 for c in (security_checks or []) if isinstance(c, dict) and _check_status(c) == "pass")
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

    # Lifecycle: prefer Meraki inventory EOX metadata; fall back to known legacy prefixes.
    _EOL_PREFIXES = (
        "MR18", "MR24", "MR26", "MR32", "MR34",
        "MS220", "MS320", "MS420",
        "MX64", "MX65", "MX80", "MX84", "MX90", "MX400", "MX600",
    )
    _eox_model_statuses: Dict[str, str] = {}
    for _eox_dev in eox_devices:
        if not isinstance(_eox_dev, dict):
            continue
        _model = str(_eox_dev.get("model") or "").strip()
        _status = str(_eox_dev.get("status") or "").strip()
        if _model and _status:
            _eox_model_statuses.setdefault(_model, _status)
    _eox_models = sorted(_eox_model_statuses)
    _heuristic_eol_models = [
        m for m, _ in top_models
        if any(str(m).upper().startswith(p) for p in _EOL_PREFIXES)
    ]
    _eol_models = _eox_models or _heuristic_eol_models
    _eox_crit_count = sum(1 for d in eox_devices if str((d or {}).get("status") or "") == "endOfSupport")
    _eox_warn_count = len(eox_devices) - _eox_crit_count
    _model_count = len(top_models)
    if eox_devices:
        _lc_rating = "crit" if _eox_crit_count else "warn"
        _lc_stat = f"{len(eox_devices)} lifecycle flag{'s' if len(eox_devices) != 1 else ''}"
        _lc_detail = ", ".join(
            f"{model} ({status})" for model, status in list(_eox_model_statuses.items())[:3]
        ) or "EOX inventory flags present"
    elif _eol_models:
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
    def _toc_item(num: int, title: str, anchor: str, subitems: str = "") -> str:
        return f"""
        <li>
          <a class="toc-link" href="#{_he(anchor)}">
            <span class="toc-num">{num}</span>
            <span class="toc-entry">{_he(title)}</span>
          </a>
          {subitems}
        </li>
        """

    def _toc_sublist(items: str) -> str:
        return f'<ol class="toc-sub">{items}</ol>' if items else ""

    toc_site_items = "".join(
        f'<li class="toc-sub-item"><a href="#network-topology">{_he(net_data["name"])}</a></li>'
        for net_data in sorted(devices_by_network.values(), key=lambda x: x["name"])
    )
    switch_deep_dive_html, toc_switch_items = _build_switch_detail_section(
        devices_by_network,
        lldp_cdp,
        switch_port_statuses_by_switch,
        switch_port_configs_by_switch,
        poe_by_serial,
        port_issues_by_switch,
        hardware_catalog,
    )
    switch_deep_dive_is_appendix = len(toc_switch_items) > 12

    def _build_switch_summary_for_main_report() -> str:
        rows = []
        switch_devices = [
            d for d in devices_avail
            if isinstance(d, dict) and d.get("productType") == "switch" and d.get("serial")
        ]
        for sw in sorted(switch_devices, key=lambda d: (str((d.get("network") or {}).get("name") or ""), str(d.get("name") or d.get("serial")))):
            serial = sw.get("serial")
            ports = switch_port_statuses_by_switch.get(serial) if isinstance(switch_port_statuses_by_switch, dict) else []
            configs = switch_port_configs_by_switch.get(serial) if isinstance(switch_port_configs_by_switch, dict) else []
            connected = sum(1 for p in ports if isinstance(p, dict) and str(p.get("status") or "").lower() == "connected") if isinstance(ports, list) else 0
            poe = poe_by_serial.get(serial, {}) if isinstance(poe_by_serial, dict) else {}
            avg_w = poe.get("avgWatts")
            issues = len(port_issues_by_switch.get(serial, [])) if isinstance(port_issues_by_switch, dict) else 0
            rows.append(
                "<tr>"
                f"<td>{_he((sw.get('network') or {}).get('name') or network_names.get(sw.get('networkId'), 'Unassigned'))}</td>"
                f"<td>{_he(sw.get('name') or serial)}<br><code>{_he(serial)}</code></td>"
                f"<td>{_model_cell(sw.get('model'))}</td>"
                f"<td>{len(ports) if isinstance(ports, list) else '—'}</td>"
                f"<td>{connected}</td>"
                f"<td>{len(configs) if isinstance(configs, list) else '—'}</td>"
                f"<td>{_he(f'{avg_w:.1f} W' if isinstance(avg_w, (int, float)) else '—')}</td>"
                f"<td>{issues}</td>"
                "</tr>"
            )
        return f"""
    <section id="switch-deep-dive" class="report-section">
      <h2>16. Switch Deep Dive Summary</h2>
      <div class="summary-card">
        <div class="summary-title">Technical Appendix Moved To Backup Settings</div>
        <div class="summary-body">
          This organization has <strong>{len(switch_devices)}</strong> switch(es), so the full per-port
          appendix is intentionally kept in the companion <strong>Backup Settings Report</strong>.
          The main report keeps the operational read concise while preserving complete port-level
          evidence, VLAN mode, PoE, LLDP/CDP, and neighbor detail in the backup packet.
        </div>
      </div>
      <table class="data dense">
        <thead>
          <tr><th>Site</th><th>Switch</th><th>Model</th><th>Ports</th><th>Connected</th><th>Configs</th><th>PoE Avg</th><th>Issues</th></tr>
        </thead>
        <tbody>{''.join(rows) if rows else '<tr><td colspan="8" class="empty-state">No switch inventory was present.</td></tr>'}</tbody>
      </table>
    </section>
        """

    switch_main_report_html = (
        _build_switch_summary_for_main_report()
        if switch_deep_dive_is_appendix
        else switch_deep_dive_html
    )
    ap_interference_html = _build_ap_interference_section(
        devices_by_network,
        channel_util,
        wireless_stats,
        switch_port_statuses_by_switch,
    )
    ap_spectrum_html = _build_ap_spectrum_report(
        devices_by_network,
        channel_util,
        wireless_stats,
        rf_profiles,
        rf_profile_assignments,
        hardware_catalog,
        wireless_design_reference,
        wireless_event_log,
    )
    config_coverage_html = _build_config_coverage_section(org_dir, networks)
    budget_forecast_html = _build_budget_forecast_section(inventory_summary, pricing_payload)
    wan_capacity_html = _build_wan_capacity_section(
        uplink_statuses,
        appliance_uplinks_usage,
        devices_avail,
        networks_by_id,
    )
    addressing_dhcp_html = _build_addressing_dhcp_section(
        networks,
        appliance_vlans,
        appliance_dhcp_subnets,
        client_records,
        devices_avail,
    )
    toc_switch_subitems = "".join(
        f'<li class="toc-sub-item"><a href="#{_he(anchor)}">{_he(label)}</a></li>'
        for anchor, label in toc_switch_items
    )
    toc_entries = [
        (1, "Executive Summary", "executive-summary", ""),
        ("Guide", "How to Use This Report", "report-guide", ""),
        (2, "Network Overview", "network-overview", ""),
        (3, "Network Topology", "network-topology", _toc_sublist(toc_site_items)),
        (4, "Traffic Flows & Bottleneck Analysis", "traffic-flows", ""),
        (5, "Device Health & Issues", "device-health", ""),
        (6, "PoE Power Analysis", "poe-analysis", ""),
        ("6A", "Battery Backup Runtime Planning", "ups-runtime", ""),
        (7, "Security Baseline", "security-baseline", ""),
        (8, "Recommendations & Implementation Plan", "recommendations", ""),
        (9, "CIS 8 Controls Assessment", "cis8", ""),
        (10, "Licensing Summary", "licensing", ""),
        (11, "Configuration Backup Coverage", "config-coverage", ""),
        (12, "Hardware Cost & Refresh Plan", "budget-forecast", ""),
        (13, "Internet Capacity & Utilization", "wan-capacity", ""),
        (14, "AP Interference Audit", "ap-interference", ""),
        (15, "Client Analysis", "client-analysis", ""),
        (
            16,
            "Switch Deep Dive Summary" if switch_deep_dive_is_appendix else "Switch Deep Dive",
            "switch-deep-dive",
            "" if switch_deep_dive_is_appendix else _toc_sublist(toc_switch_subitems),
        ),
        (17, "UniFi Comparison & Refresh Planning", "unifi-comparison", ""),
        (18, "K-12 VLAN Segmentation Reference", "vlan-reference", ""),
    ]
    backup_toc_entries = [
        (1, "Backup Packet Guide", "backup-packet-guide", ""),
        (2, "Configuration Backup Coverage", "config-coverage", ""),
        (3, "Network Overview & Addressing", "network-overview", ""),
        (4, "Security Baseline & MX Policy", "security-baseline", ""),
        (5, "Licensing Summary", "licensing", ""),
        (6, "Client Attachment Snapshot", "client-analysis", ""),
        (7, "Switch Port Appendix", "switch-deep-dive", _toc_sublist(toc_switch_subitems)),
    ]
    toc_items_html = "".join(_toc_item(*entry) for entry in toc_entries)
    backup_toc_items_html = "".join(_toc_item(*entry) for entry in backup_toc_entries)
    toc_html = f"""
    <section class="toc-page">
      <div class="toc-header">Table of Contents</div>
      <ol class="toc-list">
        {toc_items_html}
      </ol>
    </section>
    """

    toc_backup_html = f"""
    <section class="toc-page">
      <div class="toc-header">Table of Contents</div>
      <ol class="toc-list">
        {backup_toc_items_html}
      </ol>
    </section>
    """

    complete_report_name = _dated_report_name(org_name, "Complete", _now, "pdf")
    executive_report_name = _dated_report_name(org_name, "Executive_Summary", _now, "pdf")
    backup_report_name = _dated_report_name(org_name, "Backup_Settings", _now, "pdf")
    ap_spectrum_report_name = _dated_report_name(org_name, "AP_Spectrum", _now, "pdf")

    report_guide_html = f"""
    <section id="report-guide" class="report-section">
      <h2>How to Use This Report</h2>
      <p>This report package is intentionally split by audience. The complete report provides the assessment narrative and evidence path, while companion reports keep leadership review and raw configuration backup material separate.</p>
      <div class="kpi-row report-guide-grid">
        <div class="kpi">
          <div class="kpi-label">Fast Read</div>
          <div class="kpi-value">Executive Summary</div>
          <div class="kpi-note"><a href="{_he(executive_report_name)}">{_he(executive_report_name)}</a></div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Decision Path</div>
          <div class="kpi-value">Sections 1, 7, 8, 12</div>
          <div class="kpi-note">Health, security posture, priorities, and refresh planning.</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Backup Evidence</div>
          <div class="kpi-value">Backup Settings</div>
          <div class="kpi-note"><a href="{_he(backup_report_name)}">{_he(backup_report_name)}</a></div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Wireless RF</div>
          <div class="kpi-value">AP Spectrum</div>
          <div class="kpi-note"><a href="{_he(ap_spectrum_report_name)}">{_he(ap_spectrum_report_name)}</a></div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Full Context</div>
          <div class="kpi-value">Complete Report</div>
          <div class="kpi-note"><a href="{_he(complete_report_name)}">{_he(complete_report_name)}</a></div>
        </div>
      </div>
      <table class="data">
        <thead><tr><th>Reader</th><th>Start Here</th><th>Why</th></tr></thead>
        <tbody>
          <tr><td>Leadership / Finance</td><td>Executive Summary, Recommendations, Hardware Cost &amp; Refresh Plan</td><td>Shows the largest risks, renewal/refresh pressure, and recommended timing without port-level detail.</td></tr>
          <tr><td>IT Operations</td><td>Inventory, topology, client analysis, and switch summary</td><td>Connects device inventory, site layout, clients, and operational symptoms.</td></tr>
          <tr><td>Wireless / Refresh Planning</td><td>AP Spectrum Report</td><td>Provides one AP page per unit with RF bubble, overlap candidates, transmit-power context, and replacement planning notes.</td></tr>
          <tr><td>Security / Compliance</td><td>Security Baseline, MX Firewall/Filtering Policy Backup, CIS 8 Controls, Configuration Coverage</td><td>Shows control posture and the exact backup evidence available for audit review.</td></tr>
          <tr><td>Implementation Team</td><td>Backup Settings Report</td><td>Contains the detailed port/configuration appendix that supports remediation work.</td></tr>
        </tbody>
      </table>
    </section>
    """

    backup_intro_html = f"""
    <section id="backup-packet-guide" class="report-section">
      <h2>1. Backup Packet Guide</h2>
      <div class="summary-card">
        <div class="summary-title">Purpose</div>
        <div class="summary-body">
          This companion report is the configuration and evidence packet. It keeps raw settings,
          MX policy exports, addressing/DHCP, client attachment snapshots, and switch port detail
          together so the main assessment can stay focused on conclusions and recommended action.
        </div>
      </div>
      <table class="data">
        <thead><tr><th>Evidence Area</th><th>Where It Appears</th><th>Use</th></tr></thead>
        <tbody>
          <tr><td>API artifact coverage</td><td>Configuration Backup Coverage</td><td>Confirms which JSON backup files are present or not applicable.</td></tr>
          <tr><td>VLAN, subnet, DHCP</td><td>Network Overview &amp; Addressing</td><td>Documents MX interface subnets, relay/server mode, and DHCP utilization.</td></tr>
          <tr><td>Firewall and filtering</td><td>Security Baseline &amp; MX Policy</td><td>Printable L3/L7, NAT, content filtering, VPN, group policy, and syslog snapshot.</td></tr>
          <tr><td>Switch ports</td><td>Switch Port Appendix</td><td>Full per-port state, VLAN mode, PoE draw, LLDP/CDP neighbor, and issue flags.</td></tr>
        </tbody>
      </table>
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

    def _count_records(value: Any) -> int:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            total = 0
            for item in value.values():
                if isinstance(item, list):
                    total += len(item)
                elif isinstance(item, dict):
                    total += _count_records(item)
                elif item:
                    total += 1
            return total
        return 0

    def _exec_site_rows() -> str:
        rows = []
        for net_data in sorted(
            devices_by_network.values(),
            key=lambda item: (
                -sum(1 for d in item.get("devices", []) if isinstance(d, dict) and d.get("status") != "online"),
                item.get("name", ""),
            ),
        ):
            devices = [d for d in net_data.get("devices", []) if isinstance(d, dict)]
            site_total = len(devices)
            site_online = sum(1 for d in devices if d.get("status") == "online")
            site_alerting = sum(1 for d in devices if d.get("status") == "alerting")
            site_offline = sum(1 for d in devices if d.get("status") in ("offline", "dormant"))
            site_switches = sum(1 for d in devices if d.get("productType") == "switch")
            site_aps = sum(1 for d in devices if d.get("productType") == "wireless")
            site_mx = sum(1 for d in devices if d.get("productType") == "appliance")
            site_pct = round(100 * site_online / max(site_total, 1)) if site_total else 0
            rows.append(
                "<tr>"
                f"<td><strong>{_he(net_data.get('name', 'Unassigned'))}</strong></td>"
                f"<td>{site_total}</td>"
                f"<td>{site_online} / {site_total} ({site_pct}%)</td>"
                f"<td>{site_offline}</td>"
                f"<td>{site_alerting}</td>"
                f"<td>{site_mx} MX · {site_switches} MS · {site_aps} MR</td>"
                "</tr>"
            )
        return "".join(rows) or '<tr><td colspan="6" class="empty-state">No site-level device data available.</td></tr>'

    _eox_counts: Dict[str, int] = {}
    if isinstance(inventory_devices, list):
        for _device in inventory_devices:
            if not isinstance(_device, dict):
                continue
            _status = str((_device.get("eox") or {}).get("status") or "active")
            _eox_counts[_status] = _eox_counts.get(_status, 0) + 1
    _eox_risk_total = sum(count for status, count in _eox_counts.items() if status and status != "active")
    _eox_summary = ", ".join(
        f"{_he(status)}: {count}" for status, count in sorted(_eox_counts.items()) if status != "active"
    ) or "No EOL/EOS inventory flags"

    _exec_vlan_count = _count_records(appliance_vlans)
    _exec_dhcp_count = _count_records(appliance_dhcp_subnets)
    _exec_policy_count = _count_records(appliance_policy_backup)
    _exec_switch_status_count = _count_records(switch_port_statuses_by_switch)
    _exec_switch_config_count = _count_records(switch_port_configs_by_switch)
    _exec_client_count = len(client_records)

    def _confidence_badge(label: str, ok: bool, detail: str) -> str:
        cls = "badge-ok" if ok else "badge-warn"
        return (
            "<tr>"
            f"<td><strong>{_he(label)}</strong></td>"
            f'<td><span class="badge {cls}">{"High" if ok else "Partial"}</span></td>'
            f"<td>{_he(detail)}</td>"
            "</tr>"
        )

    _data_confidence_html = "".join([
        _confidence_badge(
            "Inventory and device status",
            bool(total_devices and devices_avail),
            f"{total_devices} device records with Dashboard availability status."
            if devices_avail
            else "Inventory is present, but Dashboard availability status was not captured.",
        ),
        _confidence_badge(
            "Client attachment detail",
            bool(network_clients),
            f"{_exec_client_count} wired/wireless client attachment records from network_clients.json."
            if network_clients
            else (
                f"{_exec_client_count} legacy wireless client records; wired client visibility may be incomplete."
                if wireless_clients
                else "No client detail records were captured."
            ),
        ),
        _confidence_badge(
            "VLAN and DHCP evidence",
            bool(_exec_vlan_count or _exec_dhcp_count),
            f"{_exec_vlan_count} VLAN records and {_exec_dhcp_count} DHCP scope/utilization records."
            if (_exec_vlan_count or _exec_dhcp_count)
            else "No VLAN or DHCP scope telemetry was captured.",
        ),
        _confidence_badge(
            "Firewall and filtering backup",
            bool(appliance_policy_backup),
            f"{_exec_policy_count} MX policy backup artifact group(s) captured."
            if appliance_policy_backup
            else "No MX firewall/content-filtering policy backup was captured.",
        ),
        _confidence_badge(
            "WAN uplink evidence",
            bool(uplink_statuses or appliance_uplinks_usage),
            "WAN status and/or uplink usage artifacts are present."
            if (uplink_statuses or appliance_uplinks_usage)
            else "WAN uplink status and usage telemetry were not captured.",
        ),
    ])

    _exec_price_models = pricing_payload.get("models") if isinstance(pricing_payload, dict) else {}
    _exec_price_products = pricing_payload.get("products") if isinstance(pricing_payload, dict) else {}
    _exec_unifi_map = pricing_payload.get("unifi_equivalents") if isinstance(pricing_payload, dict) else {}

    def _exec_match_prefix(model: str, mapping: Dict[str, Any]) -> str | None:
        text = str(model or "").upper()
        return next((key for key in sorted(mapping, key=len, reverse=True) if text.startswith(str(key).upper())), None)

    def _exec_product_key(entry: Any) -> str | None:
        if isinstance(entry, dict):
            value = entry.get("product_key") or entry.get("sku")
            return str(value) if value else None
        return None

    def _exec_product(product_key: str | None) -> Dict[str, Any]:
        if not product_key or not isinstance(_exec_price_products, dict):
            return {}
        product = _exec_price_products.get(product_key)
        return product if isinstance(product, dict) else {}

    def _exec_unit_price(model: str, product: Dict[str, Any]) -> int | float | None:
        value = product.get("unit_cost") if isinstance(product, dict) else None
        if isinstance(value, (int, float)):
            return value
        if not isinstance(_exec_price_models, dict):
            return None
        prefix = _exec_match_prefix(model, _exec_price_models)
        data = _exec_price_models.get(model) or _exec_price_models.get(prefix or "")
        if not isinstance(data, dict):
            return None
        value = data.get("unifi_unit_cost")
        return value if isinstance(value, (int, float)) else None

    def _exec_care_price(product: Dict[str, Any]) -> int | None:
        value = product.get("ui_care_5yr_unit_cost") if isinstance(product, dict) else None
        return int(value) if isinstance(value, (int, float)) else None

    def _exec_money(value: int | float | None) -> str:
        if not isinstance(value, (int, float)):
            return "Pricing needed"
        return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"

    _exec_migration_qty = 0
    _exec_migration_excluded = 0
    _exec_migration_total = 0
    _exec_migration_care = 0
    _exec_migration_families: Dict[str, int] = {}
    _exec_source_devices = devices_avail if isinstance(devices_avail, list) and devices_avail else inventory_devices
    for _device in _exec_source_devices if isinstance(_exec_source_devices, list) else []:
        if not isinstance(_device, dict):
            continue
        _model = str(_device.get("model") or _device.get("sku") or "").strip()
        if not _model:
            continue
        _status = str(_device.get("status") or "unknown").lower()
        if _status not in {"online", "alerting"}:
            _exec_migration_excluded += 1
            continue
        _map_key = _exec_match_prefix(_model, _exec_unifi_map) if isinstance(_exec_unifi_map, dict) else None
        if not _map_key:
            continue
        _entry = _exec_unifi_map[_map_key]
        _product = _exec_product(_exec_product_key(_entry))
        _unit = _exec_unit_price(_model, _product)
        _care = _exec_care_price(_product)
        _exec_migration_qty += 1
        _exec_migration_families[_model] = _exec_migration_families.get(_model, 0) + 1
        if isinstance(_unit, int):
            _exec_migration_total += _unit
        if isinstance(_care, int):
            _exec_migration_care += _care

    _exec_migration_note = (
        f"{_exec_migration_qty} active/alerting mapped device(s) priced from the UniFi reference; "
        f"{_exec_migration_excluded} dormant/offline/unknown device(s) excluded from the planning quote."
        if _exec_migration_qty
        else "No active/alerting devices matched the UniFi migration reference."
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

      <h3>Site Health Snapshot</h3>
      <table class="data dense">
        <thead>
          <tr><th>Site / Network</th><th>Devices</th><th>Online</th><th>Dormant / Offline</th><th>Alerting</th><th>Device Mix</th></tr>
        </thead>
        <tbody>{_exec_site_rows()}</tbody>
      </table>

      <h3>Lifecycle, Licensing &amp; Planning Snapshot</h3>
      <table class="data dense">
        <thead>
          <tr><th>Area</th><th>Executive Read</th><th>Planning Implication</th></tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Lifecycle</strong></td>
            <td>{_eox_risk_total} device(s) with EOL/EOS lifecycle flags. {_eox_summary}</td>
            <td>Use lifecycle status to prioritize refresh waves before expanding scope to healthy devices.</td>
          </tr>
          <tr>
            <td><strong>Licensing</strong></td>
            <td>{_lic_expired} expired license key(s); {_lic_active} active license record(s); {_he(_lic_mode or "unknown")} model.</td>
            <td>Resolve licensing exposure before relying on Dashboard visibility or security enforcement.</td>
          </tr>
          <tr>
            <td><strong>Migration Budget</strong></td>
            <td>{_exec_money(_exec_migration_total)} hardware planning total; {_exec_money(_exec_migration_care)} optional 5-year UI Care add-on.</td>
            <td>{_he(_exec_migration_note)}</td>
          </tr>
        </tbody>
      </table>

      <h3>Backup Evidence Captured</h3>
      <table class="data dense">
        <thead>
          <tr><th>Evidence Area</th><th>Records Captured</th><th>Where To Read It</th></tr>
        </thead>
        <tbody>
          <tr><td>Switch port status / configs</td><td>{_exec_switch_status_count} status · {_exec_switch_config_count} config</td><td>Backup Settings Report, Switch Port Appendix</td></tr>
          <tr><td>VLANs and DHCP scopes</td><td>{_exec_vlan_count} VLAN · {_exec_dhcp_count} DHCP</td><td>Complete Report Section 2 and Backup Settings Report</td></tr>
          <tr><td>Firewall, filtering, group policy, VPN, syslog</td><td>{_exec_policy_count}</td><td>Complete Report Section 7 and Backup Settings Report</td></tr>
          <tr><td>Client attachment detail</td><td>{_exec_client_count}</td><td>Complete Report Section 15 and Backup Settings Report</td></tr>
        </tbody>
      </table>

      <h3>Data Confidence Snapshot</h3>
      <table class="data dense">
        <thead>
          <tr><th>Data Area</th><th>Confidence</th><th>Interpretation</th></tr>
        </thead>
        <tbody>{_data_confidence_html}</tbody>
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
      {addressing_dhcp_html}
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
        <div class="summary-title">PoE Budget Reference Coverage</div>
        <div class="summary-body">
          {_he(poe_budget_note)}
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
        if not _is_low_speed_link(s):
            return None
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
                            f"<td>{_model_cell(_ap_model)}</td>"
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
                    f"<td>{_model_cell(_mod)}</td>"
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
              <th>Switch</th><th>Serial</th><th>Port</th><th>Errors</th>
              <th>Speed</th><th>Duplex</th><th>PoE Mode</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
        """
        for issue in switch_port_issues[:25]:
            err_display = ", ".join(issue["errors"]) if issue["errors"] else "—"
            issues_html += (
                f"<tr>"
                f"<td>{_he(issue.get('switch_name') or issue['switch'])}</td>"
                f"<td><code>{_he(issue['switch'])}</code></td>"
                f"<td>{issue['port']}</td>"
                f"<td>{_he(err_display)}</td>"
                f"<td>{_he(str(issue['speed']))}</td>"
                f"<td>{_he(str(issue['duplex']))}</td>"
                f"<td>{_he(str(issue['poeMode']))}</td>"
                f"<td>{_he(str(issue['status']))}</td>"
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
                hidden_default_count = 0
                rendered_count = 0
                for ssid in ssids:
                    if not isinstance(ssid, dict):
                        continue
                    ssid_label = ssid.get("name") or f"SSID {ssid.get('number', '')}"
                    is_default_disabled = (
                        not ssid.get("enabled")
                        and str(ssid_label).lower().startswith("unconfigured ssid")
                    )
                    if is_default_disabled:
                        hidden_default_count += 1
                        continue
                    if rendered_count >= 20:
                        continue
                    rendered_count += 1
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
                if hidden_default_count:
                    issues_html += (
                        "<tr>"
                        f"<td colspan=\"8\">{hidden_default_count} disabled default/unconfigured SSID slot(s) hidden.</td>"
                        "</tr>"
                    )
                issues_html += "</tbody></table>"

        if isinstance(wireless_mesh_statuses, dict) and wireless_mesh_statuses:
            mesh_notes = []
            for net_id, payload in wireless_mesh_statuses.items():
                if isinstance(payload, dict) and payload.get("error"):
                    error_text = str(payload.get("error") or "")
                    if "No MR repeaters found" in error_text:
                        continue
                    mesh_notes.append(f"{network_names.get(net_id, net_id)}: {error_text}")
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
        fw_status_by_key: Dict[tuple[str, str], List[str]] = {}
        fw_rows = []
        fw_items = []

        def _version_name(value: Any) -> str:
            if isinstance(value, dict):
                return str(value.get("shortName") or value.get("firmware") or "—")
            if isinstance(value, str):
                return value
            return "—"

        def _infer_product(*versions: Any) -> str:
            text = " ".join(_version_name(version) for version in versions).upper()
            if "MX " in text:
                return "appliance"
            if "MS " in text or "CS " in text or "IOS XE" in text:
                return "switch"
            if "MR " in text:
                return "wireless"
            if "MV " in text:
                return "camera"
            if "MG " in text:
                return "cellularGateway"
            return "—"

        for item in firmware_upgrades:
            if not isinstance(item, dict):
                continue
            products = item.get("products") or {}
            product_names = [name for name in ("appliance", "switch", "wireless") if products.get(name)]
            if not product_names:
                product_types = item.get("productTypes") or []
                if isinstance(product_types, list):
                    product_names = [str(product) for product in product_types]
            current_version = item.get("currentVersion") or {}
            current_name = _version_name(current_version)
            target_version = (item.get("nextUpgrade") or {}).get("toVersion") or item.get("toVersion") or {}
            available_versions = item.get("availableVersions") or []
            stable_versions = [
                version for version in available_versions
                if isinstance(version, dict) and str(version.get("releaseType", "")).lower() == "stable"
            ]
            if not target_version and stable_versions:
                target_version = stable_versions[0]
            target_name = _version_name(target_version)
            if not product_names:
                inferred = _infer_product(current_version, target_version, item.get("fromVersion"), item.get("toVersion"))
                product_names = [] if inferred == "—" else [inferred]
            net_name = (item.get("network") or {}).get("name") or (item.get("network") or {}).get("id", "—")
            if current_name != "—" or item.get("isUpgradeAvailable") or item.get("nextUpgrade"):
                product_label = ", ".join(product_names) or _infer_product(current_version, target_version)
                fw_status_by_key[(net_name, product_label)] = [
                    net_name,
                    product_label,
                    current_name,
                    target_name,
                    "Yes" if item.get("isUpgradeAvailable") else "No",
                    str(item.get("upgradeStrategy") or "—"),
                ]
            dt = _parse_dt(item.get("time", ""))
            if not dt and item.get("completedAt"):
                try:
                    dt = datetime.strptime(item["completedAt"], "%Y-%m-%d %H:%M:%S UTC")
                except ValueError:
                    dt = None
            fw_items.append((dt, item))
        fw_items.sort(key=lambda x: x[0] or datetime.min, reverse=True)
        fw_status_rows = sorted(fw_status_by_key.values(), key=lambda row: (row[0], row[1]))
        if fw_status_rows:
            issues_html += render_section(
                "Firmware Status & Available Versions",
                [["Network", "Product", "Current", "Dashboard Target / Stable", "Upgrade Available", "Strategy"]] + fw_status_rows,
            )
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
        eox_rows = []
        for device in eox_devices[:20]:
            support_dt = _parse_dt(device.get("endOfSupport") or "")
            row_class = "row-eos-announced"
            if support_dt:
                now_for_compare = _now
                if support_dt.tzinfo and not now_for_compare.tzinfo:
                    now_for_compare = now_for_compare.replace(tzinfo=support_dt.tzinfo)
                if support_dt <= now_for_compare + timedelta(days=730):
                    row_class = "row-eos-critical"
            eox_rows.append(
                "<tr class=\"%s\"><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    row_class,
                    _he(device.get("name", "—")),
                    _he(device.get("model", "—")),
                    _he(device.get("network", "—")),
                    _he(device.get("status", "—")),
                    _he(str(device.get("endOfSale") or "—")),
                    _he(str(device.get("endOfSupport") or "—")),
                )
            )
        issues_html += (
            "<h2>End-of-Life / End-of-Support Inventory</h2>"
            '<table class="data">'
            "<thead><tr><th>Device</th><th>Model</th><th>Network</th><th>Status</th><th>End of Sale</th><th>End of Support</th></tr></thead>"
            "<tbody>"
            + "".join(eox_rows)
            + "</tbody></table>"
            '<div class="summary-card"><div class="summary-body">'
            '<span class="badge badge-fail">Red</span> End of support is within 2 years. '
            '<span class="badge badge-warn">Yellow</span> EOL/EOS has been announced but support is more than 2 years out or no support date was provided.'
            "</div></div>"
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
        recent = [
            a for a in alert_items
            if a["dt"] and a["dt"] >= _now.replace(tzinfo=a["dt"].tzinfo) - timedelta(days=30)
        ]
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

    has_issue_content = any(
        [
            switch_port_issues,
            config_issues,
            high_util_devices,
            eox_devices,
            _lic_expired,
            isinstance(alerts_history, dict) and any(alerts_history.values()),
        ]
    )
    if not has_issue_content:
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
        poe_switch_rows = []
        for s in poe_switches[:20]:
            serial = s.get("serial", "")
            device = device_by_serial.get(serial) or {}
            model = str(device.get("model") or "")
            budget = _known_poe_budget(model)
            observed_watts = float(s.get("avgWatts", 0) or 0)
            headroom = (
                f"{max(0.0, float(budget) - observed_watts):.1f} W"
                if budget is not None
                else "Unknown"
            )
            switch_name = device.get("name") or model or serial
            poe_switch_rows.append(
                [
                    f"{switch_name} ({serial})" if switch_name != serial else serial,
                    model or "Unknown",
                    f"{observed_watts:.1f} W",
                    f"{budget:g} W" if budget is not None else "Unknown",
                    headroom,
                    f"{float(s.get('powerUsageInWh', 0) or 0):.1f} Wh",
                ]
            )
        poe_html += render_section(
            "PoE Consumption by Switch (24 h average)",
            poe_switch_rows,
            headers=["Switch", "Model", "Observed Avg", "Known Budget", "Headroom", "24 h Energy"],
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
    # SECTION 6A: UPS RUNTIME PLANNING
    # =========================================================
    ups_power_plan = _build_ups_power_plan(
        org_name,
        switch_devices,
        poe_by_serial,
        ups_payload,
        hardware_catalog,
        _now,
    )
    ups_meta = ups_payload.get("meta") if isinstance(ups_payload, dict) else {}
    ups_products = ups_payload.get("products") if isinstance(ups_payload, dict) else {}
    bx_ref = ups_products.get("BX1500M") if isinstance(ups_products, dict) else {}
    smx_ref = ups_products.get("SMX2200RMLV2U") if isinstance(ups_products, dict) else {}

    ups_rows: List[List[str]] = []
    for item in ups_power_plan.get("switches", []):
        target_stack = (item.get("runtimeEstimates") or {}).get("SMX2200RMLV2UTargetStack") or {}
        target_label = str(target_stack.get("label") or "No listed stack reaches target")
        if target_stack.get("runtimeLabel"):
            target_label = f"{target_label} ({target_stack.get('runtimeLabel')})"
        ups_rows.append(
            [
                f"{item.get('switchName')} ({item.get('serial')})"
                if item.get("serial") and item.get("switchName") != item.get("serial")
                else str(item.get("switchName") or item.get("serial") or "Unknown"),
                str(item.get("model") or "Unknown"),
                f"{float(item.get('observedPoeAvgWatts') or 0):.1f} W",
                f"{float(item.get('chassisEstimateWatts') or 0):.1f} W",
                f"{float(item.get('baseModeledLoadWatts') or 0):.1f} W",
                f"{float(item.get('sizingLoadWatts') or 0):.1f} W",
                ((item.get("runtimeEstimates") or {}).get("BX1500M") or {}).get("runtimeLabel", "Over UPS rating"),
                ((item.get("runtimeEstimates") or {}).get("SMX2200RMLV2UBase") or {}).get("runtimeLabel", "Over UPS rating"),
                target_label,
                target_stack.get("estimatedCostLabel", "Pricing needed"),
            ]
        )

    ups_summary = ups_power_plan.get("summary") or {}
    ups_assumptions_summary = ups_power_plan.get("planningAssumptions") or {}
    ups_target_hours = float(ups_assumptions_summary.get("targetRuntimeHours") or 10)
    avg_ups_load = float(ups_summary.get("averageSizingLoadWatts") or 0)
    max_ups_load = float(ups_summary.get("maxSizingLoadWatts") or 0)
    bx_max = bx_ref.get("max_watts") if isinstance(bx_ref, dict) else None
    smx_max = smx_ref.get("max_watts") if isinstance(smx_ref, dict) else None
    smx_unit = smx_ref.get("unit_cost") if isinstance(smx_ref, dict) else None
    smx_ext = smx_ref.get("external_battery_unit_cost") if isinstance(smx_ref, dict) else None
    ups_switch_items = ups_power_plan.get("switches", []) if isinstance(ups_power_plan.get("switches"), list) else []
    target_stacks = [
        ((item.get("runtimeEstimates") or {}).get("SMX2200RMLV2UTargetStack") or {})
        for item in ups_switch_items
        if isinstance(item, dict)
    ]
    target_costs = [
        float(stack.get("estimatedCost"))
        for stack in target_stacks
        if isinstance(stack.get("estimatedCost"), (int, float))
    ]
    total_target_cost = sum(target_costs) if target_costs else None
    max_external_batteries = max(
        [int(stack.get("externalBatteryCount") or 0) for stack in target_stacks if stack.get("externalBatteryCount") is not None],
        default=0,
    )
    no_target_stack_count = sum(1 for stack in target_stacks if stack.get("runtimeMinutes") is None)
    bx_runtime_minutes = [
        float(((item.get("runtimeEstimates") or {}).get("BX1500M") or {}).get("runtimeMinutes"))
        for item in ups_switch_items
        if isinstance((((item.get("runtimeEstimates") or {}).get("BX1500M") or {}).get("runtimeMinutes")), (int, float))
    ]
    smx_base_runtime_minutes = [
        float(((item.get("runtimeEstimates") or {}).get("SMX2200RMLV2UBase") or {}).get("runtimeMinutes"))
        for item in ups_switch_items
        if isinstance((((item.get("runtimeEstimates") or {}).get("SMX2200RMLV2UBase") or {}).get("runtimeMinutes")), (int, float))
    ]
    smx_base_below_target_count = sum(1 for mins in smx_base_runtime_minutes if mins < ups_target_hours * 60)
    site_plan_summary = ups_power_plan.get("sites") if isinstance(ups_power_plan.get("sites"), dict) else {}
    heaviest_site = ""
    if site_plan_summary:
        heaviest_site, _heaviest_data = max(
            site_plan_summary.items(),
            key=lambda item: float((item[1] or {}).get("totalSizingLoadWatts") or 0) if isinstance(item[1], dict) else 0,
        )
    battery_recommendations = []
    if ups_rows:
        battery_recommendations.append(
            f"Use the Smart-UPS X stack as the planning standard for network closets that need the {ups_target_hours:g} hour runtime target; the BX1500M should be treated as a short-runtime single-switch fallback."
        )
        if total_target_cost is not None:
            battery_recommendations.append(
                f"Budget approximately {_format_money(total_target_cost)} for the modeled target-runtime switch stacks in this report, before installation, electrical work, tax, shipping, or spares."
            )
        if smx_base_below_target_count:
            battery_recommendations.append(
                f"The base SMX2200RMLV2U alone is below the {ups_target_hours:g} hour target for {smx_base_below_target_count} switch load(s), so external battery modules are required where extended runtime is expected."
            )
        if max_external_batteries:
            battery_recommendations.append(
                f"The largest modeled stack requires {max_external_batteries} external battery module(s); validate rack space, circuit capacity, and battery maintenance before quoting."
            )
        if no_target_stack_count:
            battery_recommendations.append(
                f"{no_target_stack_count} switch load(s) did not reach the target with the available runtime chart, so those closets need manual UPS sizing."
            )
        if heaviest_site:
            battery_recommendations.append(
                f"Highest aggregate sizing load is at {heaviest_site}; start validation there before standardizing smaller closets."
            )
    else:
        battery_recommendations.append("No switch loads were available, so no UPS purchase action should be taken from this report yet.")
    bx_window = (
        f"{_format_runtime_minutes(min(bx_runtime_minutes))} to {_format_runtime_minutes(max(bx_runtime_minutes))}"
        if bx_runtime_minutes
        else "not available"
    )
    smx_base_window = (
        f"{_format_runtime_minutes(min(smx_base_runtime_minutes))} to {_format_runtime_minutes(max(smx_base_runtime_minutes))}"
        if smx_base_runtime_minutes
        else "not available"
    )
    ups_source_links = ""
    if isinstance(ups_meta, dict) and isinstance(ups_meta.get("sources"), list):
        links = []
        for source in ups_meta.get("sources", [])[:4]:
            if not isinstance(source, dict) or not source.get("url"):
                continue
            links.append(
                f'<a href="{_he(str(source.get("url")))}">{_he(str(source.get("title") or source.get("url")))}</a>'
            )
        if links:
            ups_source_links = "<p>Runtime reference sources: " + "; ".join(links) + ".</p>"

    ups_html = f"""
    <section id="ups-runtime" class="report-section">
      <h2>6A. Battery Backup Runtime Planning</h2>
      <div class="kpi-row">
        <div class="kpi">
          <div class="kpi-label">Switches Sized</div>
          <div class="kpi-value">{len(ups_rows)}</div>
          <div class="kpi-note">One UPS stack per listed switch load</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Average Sizing Load</div>
          <div class="kpi-value">{avg_ups_load:.1f} W</div>
          <div class="kpi-note">Modeled load + 10% buffer</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Largest Sizing Load</div>
          <div class="kpi-value">{max_ups_load:.1f} W</div>
          <div class="kpi-note">Used for closet-level sizing checks</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Planning Target</div>
          <div class="kpi-value">{ups_target_hours:g} hours</div>
          <div class="kpi-note">Smart-UPS external battery stack</div>
        </div>
      </div>
      <div class="summary-card">
        <div class="summary-title">Executive Recommendation</div>
        <div class="summary-body">
          The practical planning recommendation is to use the APC Smart-UPS X plus external battery modules for closets where extended runtime matters, and reserve the BX1500M class for small, non-critical edge switches where short runtime is acceptable.
          <ul>
            {''.join(f'<li>{_he(point)}</li>' for point in battery_recommendations)}
          </ul>
          <strong>Runtime read:</strong> BX1500M estimated range is {_he(bx_window)} across modeled switch loads; base SMX2200RMLV2U estimated range is {_he(smx_base_window)} before adding external modules.
        </div>
      </div>
      <div class="summary-card">
        <div class="summary-title">Sizing Method</div>
        <div class="summary-body">
          The estimate models each switch as Meraki-observed average PoE draw plus a conservative chassis/base load by switch family, then applies a <strong>10% planning buffer</strong> before sizing UPS runtime.
          The same data is saved beside the report as <code>ups_switch_power_plan.json</code> for future planning and review. BX1500M is treated as a small single-switch option
          {f"rated to {float(bx_max):g} W" if isinstance(bx_max, (int, float)) else ""}; SMX2200RMLV2U is treated as the rack/tower option
          {f"rated to {float(smx_max):g} W" if isinstance(smx_max, (int, float)) else ""} with {smx_ref.get("external_battery_sku", "external battery modules") if isinstance(smx_ref, dict) else "external battery modules"} for extended runtime.
          Runtime varies with battery age, temperature, load mix, and calibration, so these are planning estimates rather than procurement guarantees.
        </div>
      </div>
    """
    if ups_rows:
        ups_html += render_section(
            "UPS Runtime Estimate by Switch",
            ups_rows,
            headers=[
                "Switch",
                "Model",
                "Observed PoE Avg",
                "Chassis Est.",
                "Modeled Load",
                "Sizing Load (+10%)",
                "BX1500M ETA",
                "SMX Base ETA",
                f"SMX Stack for {ups_target_hours:g}h",
                "Stack Cost",
            ],
        )
        ups_html += (
            '<div class="summary-card">'
            '<div class="summary-title">Pricing Reference</div>'
            '<div class="summary-body">'
            f'Smart-UPS X controller: {_format_money(smx_unit)} each. '
            f'External battery module: {_format_money(smx_ext)} each. '
            'The BX1500M option is included for runtime planning, but no unit price was provided in the current reference data.'
            f'{ups_source_links}'
            '</div></div>'
        )
    else:
        ups_html += (
            '<div class="summary-card">'
            '<div class="summary-body">No switch inventory was available for UPS runtime planning.</div>'
            "</div>"
        )
    ups_html += "</section>"

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

    appliance_policy_html = _build_appliance_policy_section(networks, appliance_policy_backup)

    security_html = f"""
    <section id="security-baseline" class="report-section">
      <h2>7. Security &amp; Compliance</h2>
      <p>This section evaluates security posture from two angles: an appliance-level baseline
         check (AMP, IDS/IPS, spoof protection, and internet exposure), printable MX policy
         backups, and a CIS Controls mapping in the following section. Together they form the
         security health layer of this network audit.</p>

      <div class="summary-card">
        <div class="summary-title">Security Posture Summary</div>
        <div class="summary-body">
          {_sec_posture}
          <br><br>
          <strong>Firewall &amp; Internet Exposure:</strong> {_pf_note}
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
      {appliance_policy_html}
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
    connection_counts: Dict[str, int] = {}
    top_client_rows: list[list[str]] = []
    rssi_buckets = {"Excellent (>-60)": 0, "Good (-60 to -70)": 0,
                    "Fair (-70 to -80)": 0, "Poor (<-80)": 0}

    for cl in client_records:
        ssid = cl.get("ssid") or "Unknown"
        ssid_counts[ssid] = ssid_counts.get(ssid, 0) + 1

        os_raw = cl.get("os") or cl.get("deviceTypePrediction") or "Unknown"
        os_counts[os_raw] = os_counts.get(os_raw, 0) + 1

        vlan = str(cl.get("vlan") or cl.get("vlanId") or "—")
        vlan_counts[vlan] = vlan_counts.get(vlan, 0) + 1

        auth = cl.get("status") or cl.get("authType") or "Unknown"
        auth_counts[auth] = auth_counts.get(auth, 0) + 1

        connection = cl.get("recentDeviceConnection") or ("Wireless" if cl.get("ssid") else "Unknown")
        connection_counts[connection] = connection_counts.get(connection, 0) + 1

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

    def _usage_total_kb(client: Dict[str, Any]) -> float:
        usage = client.get("usage") or {}
        sent = usage.get("sent") if isinstance(usage, dict) else 0
        recv = usage.get("recv") if isinstance(usage, dict) else 0
        try:
            return float(sent or 0) + float(recv or 0)
        except (TypeError, ValueError):
            return 0.0

    for cl in sorted(client_records, key=_usage_total_kb, reverse=True)[:15]:
        top_client_rows.append([
            cl.get("description") or cl.get("mac") or cl.get("id") or "Unknown",
            cl.get("recentDeviceConnection") or ("Wireless" if cl.get("ssid") else "Unknown"),
            cl.get("recentDeviceName") or cl.get("recentDeviceSerial") or "Unknown",
            cl.get("ssid") or "—",
            str(cl.get("vlan") or cl.get("namedVlan") or "—"),
            _format_usage_kb(int(_usage_total_kb(cl))),
        ])

    def _top_rows(d: Dict[str, int], limit: int = 10) -> str:
        rows = sorted(d.items(), key=lambda x: x[1], reverse=True)[:limit]
        return "".join(f"<tr><td>{_he(str(k))}</td><td>{v}</td></tr>" for k, v in rows)

    rssi_rows = "".join(
        f"<tr><td>{bucket}</td><td>{cnt}</td></tr>"
        for bucket, cnt in rssi_buckets.items()
    )
    overview_rows: list[list[str]] = []
    overview_totals = {
        "clients": 0,
        "heavy": 0,
        "average_kb": 0,
        "heavy_average_kb": 0,
        "networks": 0,
    }
    if isinstance(clients_overview_raw, dict):
        for net_id, overview in sorted(clients_overview_raw.items(), key=lambda item: network_names.get(item[0], item[0])):
            if not isinstance(overview, dict) or overview.get("error"):
                continue
            counts = overview.get("counts") or {}
            usages = overview.get("usages") or {}
            total_clients = int(counts.get("total") or 0)
            heavy_clients = int(counts.get("withHeavyUsage") or 0)
            average_kb = int(usages.get("average") or 0)
            heavy_average_kb = int(usages.get("withHeavyUsageAverage") or 0)
            overview_totals["clients"] += total_clients
            overview_totals["heavy"] += heavy_clients
            overview_totals["average_kb"] += average_kb
            overview_totals["heavy_average_kb"] += heavy_average_kb
            overview_totals["networks"] += 1
            overview_rows.append([
                network_names.get(net_id, net_id),
                str(total_clients),
                str(heavy_clients),
                _format_usage_kb(average_kb),
                _format_usage_kb(heavy_average_kb),
            ])

    client_source = "network_clients.json" if network_clients else "wireless_clients.json"
    if client_records:
        client_tables = f"""
        <div class="summary-card">
          <div class="summary-title">Source Data Coverage</div>
          <div class="summary-body">
            Client detail source: <code>{_he(client_source)}</code>. The preferred source is
            <code>network_clients.json</code> from <code>GET /networks/{{networkId}}/clients</code>,
            because it includes wired and wireless clients. Older backups may only include
            wireless-only fallback data.
          </div>
        </div>

        <h3>Clients by Connection Type</h3>
        <table class="data">
          <thead><tr><th>Connection Type</th><th>Client Count</th></tr></thead>
          <tbody>{_top_rows(connection_counts)}</tbody>
        </table>

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

        <h3>Top Clients by Usage</h3>
        <table class="data">
          <thead><tr><th>Client</th><th>Connection</th><th>Recent Device</th><th>SSID</th><th>VLAN</th><th>Usage</th></tr></thead>
          <tbody>{''.join('<tr>' + ''.join(f'<td>{_he(str(cell))}</td>' for cell in row) + '</tr>' for row in top_client_rows)}</tbody>
        </table>
        """
    else:
        client_tables = """
        <div class="summary-card">
          <div class="summary-title">Source Data Coverage</div>
          <div class="summary-body">
            No client detail records were available in this backup. Current backups should collect
            <code>network_clients.json</code> from <code>GET /networks/{networkId}/clients</code>.
            Older backups may only have <code>wireless_clients.json</code>, which does not cover
            wired clients and may be unavailable in current Dashboard API versions.
          </div>
        </div>"""
        if overview_rows:
            average_usage = int(overview_totals["average_kb"] / max(overview_totals["networks"], 1))
            heavy_average_usage = int(overview_totals["heavy_average_kb"] / max(overview_totals["networks"], 1))
            client_tables += render_section(
                "Client Overview Summary",
                [
                    ["Metric", "Value"],
                    ["Networks with overview data", str(overview_totals["networks"])],
                    ["Total clients", str(overview_totals["clients"])],
                    ["Heavy-usage clients", str(overview_totals["heavy"])],
                    ["Average usage per network", _format_usage_kb(average_usage)],
                    ["Average heavy-client usage per network", _format_usage_kb(heavy_average_usage)],
                ],
            )
            client_tables += render_section(
                "Client Overview by Network",
                [["Network", "Clients", "Heavy Usage", "Avg Usage", "Heavy Avg Usage"]] + overview_rows,
            )

    client_analysis_html = f"""
    <section id="client-analysis" class="report-section">
      <h2>15. Client Analysis</h2>
      <p>Analysis of <strong>{len(client_records)}</strong> client detail record(s)
         and <strong>{overview_totals["networks"]}</strong> network overview record(s) captured
         in this backup. Network client detail includes recent wired/wireless attachment, VLAN,
         SSID where applicable, OS/device prediction, and usage when returned by the Meraki API.</p>
      {client_tables}
    </section>
    """

    # =========================================================
    # SECTION 17: UNIFI COMPARISON & REFRESH PLANNING
    # =========================================================
    # Equivalent mappings are maintained in reporting/reference/pricing_reference.json.
    # Org-local pricing.json still wins, because reseller and E-rate pricing varies by client.
    _UNIFI_MAP = pricing_payload.get("unifi_equivalents") if isinstance(pricing_payload, dict) else {}
    _PRICE_MODELS = pricing_payload.get("models") if isinstance(pricing_payload, dict) else {}
    _PRICE_PRODUCTS = pricing_payload.get("products") if isinstance(pricing_payload, dict) else {}
    _PRICE_META = pricing_payload.get("meta") if isinstance(pricing_payload, dict) else {}
    _PRICE_UPDATED = str((_PRICE_META or {}).get("updated") or REPORT_VERSION)
    _PRICE_CURRENCY = str((_PRICE_META or {}).get("currency") or "USD")

    def _match_prefix(model: str, mapping: Dict[str, Any]) -> str | None:
        text = str(model or "").upper()
        return next((key for key in sorted(mapping, key=len, reverse=True) if text.startswith(str(key).upper())), None)

    def _price_model_data(model: str) -> Dict[str, Any]:
        if not isinstance(_PRICE_MODELS, dict):
            return {}
        exact = _PRICE_MODELS.get(model)
        prefix_key = _match_prefix(model, _PRICE_MODELS)
        data = exact if isinstance(exact, dict) else _PRICE_MODELS.get(prefix_key or "")
        if not isinstance(data, dict):
            return {}
        return data

    def _unit_price(model: str, field: str) -> int | float | None:
        data = _price_model_data(model)
        if not data:
            return None
        value = data.get(field)
        return value if isinstance(value, (int, float)) else None

    def _money(value: int | float | None) -> str:
        if not isinstance(value, (int, float)):
            return "Pricing needed"
        return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"

    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _price_confidence_badge(label: str) -> str:
        normalized = str(label or "Reference").strip()
        css = "badge-info"
        if normalized.lower().startswith("used"):
            css = "badge-warn"
        elif normalized.lower().startswith("quote"):
            css = "badge-fail"
        elif normalized.lower().startswith("client"):
            css = "badge-ok"
        return f'<span class="badge {css}">{_he(normalized)}</span>'

    def _product(product_key: str | None) -> Dict[str, Any]:
        if not product_key or not isinstance(_PRICE_PRODUCTS, dict):
            return {}
        data = _PRICE_PRODUCTS.get(product_key)
        return data if isinstance(data, dict) else {}

    def _entry_product_key(entry: Any) -> str | None:
        if isinstance(entry, dict):
            value = entry.get("product_key") or entry.get("sku")
            return str(value) if value else None
        return None

    def _entry_label(entry: Any, product: Dict[str, Any]) -> str:
        if isinstance(entry, dict):
            for key in ("name", "label", "equivalent"):
                if entry.get(key):
                    return str(entry[key])
        if product:
            return str(product.get("name") or product.get("sku") or "UniFi equivalent")
        return str(entry or "UniFi equivalent")

    def _product_unit_cost(product: Dict[str, Any], fallback: int | float | None = None) -> int | float | None:
        value = product.get("unit_cost") if isinstance(product, dict) else None
        return value if isinstance(value, (int, float)) else fallback

    def _product_care_cost(product: Dict[str, Any]) -> int | None:
        value = product.get("ui_care_5yr_unit_cost") if isinstance(product, dict) else None
        return int(value) if isinstance(value, (int, float)) else None

    def _product_cyber_cost(product: Dict[str, Any]) -> int | None:
        value = product.get("cybersecure_annual_unit_cost") if isinstance(product, dict) else None
        return int(value) if isinstance(value, (int, float)) else None

    def _product_source(product: Dict[str, Any]) -> str:
        source = str(product.get("source_url") or "").strip() if isinstance(product, dict) else ""
        label = str(product.get("source_label") or "").strip() if isinstance(product, dict) else ""
        if not source:
            return _he(label or "Reference")
        return f'<a href="{_he(source)}">{_he(label or "Ubiquiti Store")}</a>'

    def _product_price_confidence(product: Dict[str, Any]) -> str:
        if not isinstance(product, dict):
            return "Reference"
        explicit = str(product.get("pricing_confidence") or "").strip()
        if explicit:
            return explicit
        if str(product.get("category") or "") == "meraki_used":
            return "Used-market"
        if str(product.get("vendor") or "").lower() == "ubiquiti":
            return "Public MSRP"
        return "Reference"

    def _meraki_price_source(model: str) -> str:
        data = _price_model_data(model)
        source = str(data.get("meraki_unit_source") or "").strip() if data else ""
        return source or "Quote needed"

    def _meraki_price_confidence(model: str) -> str:
        source = _meraki_price_source(model).lower()
        if "networktigers" in source or "used" in source:
            return "Used-market"
        if source == "quote needed":
            return "Quote needed"
        return "Client quote"

    def _model_counts_for_refresh() -> List[Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        source_devices = devices_avail if isinstance(devices_avail, list) and devices_avail else inventory_devices
        production_statuses = {"online", "alerting"}
        saw_model = False
        for device in source_devices if isinstance(source_devices, list) else []:
            if not isinstance(device, dict):
                continue
            model = str(device.get("model") or device.get("sku") or "").strip()
            if not model:
                continue
            saw_model = True
            status = str(device.get("status") or "unknown").strip().lower()
            row = rows.setdefault(
                model,
                {"model": model, "inventory_qty": 0, "quoted_qty": 0, "excluded_qty": 0, "excluded_statuses": {}},
            )
            row["inventory_qty"] += 1
            if status in production_statuses:
                row["quoted_qty"] += 1
            else:
                row["excluded_qty"] += 1
                excluded = row["excluded_statuses"]
                excluded[status or "unknown"] = excluded.get(status or "unknown", 0) + 1
        if not saw_model:
            for model, count in top_models:
                try:
                    qty = int(count)
                except (TypeError, ValueError):
                    continue
                rows[str(model)] = {
                    "model": str(model),
                    "inventory_qty": qty,
                    "quoted_qty": qty,
                    "excluded_qty": 0,
                    "excluded_statuses": {},
                }
        return sorted(rows.values(), key=lambda item: (-int(item["quoted_qty"]), str(item["model"])))

    def _excluded_status_text(row: Dict[str, Any]) -> str:
        statuses = row.get("excluded_statuses")
        if not isinstance(statuses, dict) or not statuses:
            return "—"
        return ", ".join(f"{_he(k)}: {v}" for k, v in sorted(statuses.items()))

    def _connected_sfp_summary() -> Tuple[int, int, int]:
        total_sfp = 0
        connected_sfp = 0
        uplink_sfp = 0
        raw = switch_port_statuses_by_switch if isinstance(switch_port_statuses_by_switch, dict) else {}
        for ports in raw.values():
            if not isinstance(ports, list):
                continue
            for port in ports:
                if not isinstance(port, dict):
                    continue
                port_id = str(port.get("portId") or "")
                if not _is_sfp_like_port(port_id):
                    continue
                total_sfp += 1
                if str(port.get("status") or "").lower() == "connected":
                    connected_sfp += 1
                if port.get("isUplink"):
                    uplink_sfp += 1
        return total_sfp, connected_sfp, uplink_sfp

    _unifi_rows = ""
    _meraki_total = 0
    _unifi_total = 0
    _unifi_care_total = 0
    _unifi_cyber_annual_total = 0
    _priced_rows = 0
    _unifi_priced_rows = 0
    _mapped_rows = 0
    _mapped_quoted_qty = 0
    _eol_models_mapped: List[str] = []
    _catalog_models = _model_counts_for_refresh()
    _inventory_refresh_qty = sum(int(row.get("inventory_qty") or 0) for row in _catalog_models)
    _quoted_refresh_qty = sum(int(row.get("quoted_qty") or 0) for row in _catalog_models)
    _excluded_refresh_qty = sum(int(row.get("excluded_qty") or 0) for row in _catalog_models)
    _excluded_status_totals: Dict[str, int] = {}
    for _row in _catalog_models:
        for _status, _status_count in (_row.get("excluded_statuses") or {}).items():
            _excluded_status_totals[str(_status)] = _excluded_status_totals.get(str(_status), 0) + int(_status_count or 0)
    _excluded_status_summary = ", ".join(
        f"{_he(status)}: {count}" for status, count in sorted(_excluded_status_totals.items())
    ) or "none"
    _category_totals: Dict[str, Dict[str, int]] = {}

    for _row in _catalog_models:
        _model = str(_row.get("model") or "")
        _count = int(_row.get("quoted_qty") or 0)
        _inventory_count = int(_row.get("inventory_qty") or 0)
        _excluded_count = int(_row.get("excluded_qty") or 0)
        if _count <= 0:
            continue
        _model_text = str(_model)
        _mprefix = _model_text.upper()
        _map_key = _match_prefix(_model_text, _UNIFI_MAP)
        if not _map_key:
            continue
        _mapped_rows += 1
        _mapped_quoted_qty += _count
        _entry = _UNIFI_MAP[_map_key]
        _product_key = _entry_product_key(_entry)
        _product_data = _product(_product_key)
        _product_category = str(_product_data.get("category") or "uncategorized")
        _unifi_name = _entry_label(_entry, _product_data)
        _rationale = str(_entry.get("rationale") or "") if isinstance(_entry, dict) else ""
        _is_eol = any(_mprefix.startswith(p) for p in _EOL_PREFIXES)
        _meraki_price = _unit_price(_model_text, "meraki_unit_cost")
        _meraki_source = _meraki_price_source(_model_text) if _is_number(_meraki_price) else "Quote needed"
        _meraki_confidence = _meraki_price_confidence(_model_text)
        _unifi_price = _product_unit_cost(_product_data, _unit_price(_model_text, "unifi_unit_cost"))
        _unifi_confidence = _product_price_confidence(_product_data) if _is_number(_unifi_price) else "Quote needed"
        _ui_care_price = _product_care_cost(_product_data)
        _cyber_annual = _product_cyber_cost(_product_data)
        _row_mx = _meraki_price * _count if _is_number(_meraki_price) else None
        _row_ux = _unifi_price * _count if _is_number(_unifi_price) else None
        _row_care = _ui_care_price * _count if _is_number(_ui_care_price) else None
        _row_cyber = _cyber_annual * _count if _is_number(_cyber_annual) else None
        if _is_number(_row_mx):
            _meraki_total += _row_mx
        if _is_number(_row_ux):
            _unifi_total += _row_ux
            _unifi_priced_rows += 1
        if _is_number(_row_care):
            _unifi_care_total += _row_care
        if _is_number(_row_cyber):
            _unifi_cyber_annual_total += _row_cyber
        _bucket = _category_totals.setdefault(_product_category, {"hardware": 0, "care": 0, "cyber": 0, "qty": 0})
        _bucket["qty"] += _count
        if _is_number(_row_ux):
            _bucket["hardware"] += _row_ux
        if _is_number(_row_care):
            _bucket["care"] += _row_care
        if _is_number(_row_cyber):
            _bucket["cyber"] += _row_cyber
        if _is_number(_row_mx) and _is_number(_row_ux):
            _priced_rows += 1
        if _is_eol:
            _eol_models_mapped.append(_model_text)
        _unifi_rows += (
            f"<tr>"
            f"<td>{_he(_model)}</td>"
            f"<td>{_inventory_count}</td>"
            f"<td>{_count}</td>"
            f"<td>{_excluded_count}<br><span class=\"muted\">{_excluded_status_text(_row)}</span></td>"
            f"<td>{_he(_unifi_name)}{f'<br><span class=\"muted\">{_he(_rationale)}</span>' if _rationale else ''}</td>"
            f"<td>{_money(_meraki_price)}</td>"
            f"<td><span class=\"muted\">{_he(_meraki_source)}</span><br>{_price_confidence_badge(_meraki_confidence)}</td>"
            f"<td>{_money(_unifi_price)}<br>{_price_confidence_badge(_unifi_confidence)}</td>"
            f"<td>{_money(_ui_care_price)}</td>"
            f"<td>{_money(_row_mx)}</td>"
            f"<td>{_money(_row_ux)}</td>"
            f"<td>{_money(_row_care)}</td>"
            f'<td>{"EOL" if _is_eol else "—"}</td>'
            f"</tr>"
        )

    _savings = _meraki_total - _unifi_total if _priced_rows else None
    _savings_pct = round(100 * _savings / _meraki_total) if _priced_rows and _meraki_total else None
    _sfp_total, _connected_sfp, _uplink_sfp = _connected_sfp_summary()
    _aggregation_rows = ""
    _aggregation_total = 0
    _aggregation_care_total = 0
    if _connected_sfp >= 9:
        _agg_key = "USW-Pro-Aggregation"
        _agg_qty = max(1, math.ceil(_connected_sfp / 28))
        _agg_reason = (
            f"{_connected_sfp} connected SFP/module ports were observed. "
            "Use a 32-port aggregation switch as a planning reference for a main closet/core design."
        )
    elif _connected_sfp > 0:
        _agg_key = "USW-Aggregation"
        _agg_qty = 1
        _agg_reason = (
            f"{_connected_sfp} connected SFP/module port(s) were observed. "
            "An 8-port aggregation switch may be sufficient if the design stays small."
        )
    else:
        _agg_key = ""
        _agg_qty = 0
        _agg_reason = "No connected SFP/module ports were observed in this backup."
    if _agg_key:
        _agg_product = _product(_agg_key)
        _agg_unit = _product_unit_cost(_agg_product)
        _agg_care = _product_care_cost(_agg_product)
        _agg_total = _agg_unit * _agg_qty if _is_number(_agg_unit) else None
        _agg_care_total = _agg_care * _agg_qty if _is_number(_agg_care) else None
        if _is_number(_agg_total):
            _aggregation_total += _agg_total
            _category_totals.setdefault("aggregation", {"hardware": 0, "care": 0, "cyber": 0, "qty": 0})["hardware"] += _agg_total
            _category_totals["aggregation"]["qty"] += _agg_qty
        if _is_number(_agg_care_total):
            _aggregation_care_total += _agg_care_total
            _category_totals.setdefault("aggregation", {"hardware": 0, "care": 0, "cyber": 0, "qty": 0})["care"] += _agg_care_total
        _aggregation_rows = (
            "<tr>"
            f"<td>{_he(_agg_product.get('name') or _agg_key)}</td>"
            f"<td>{_agg_qty}</td>"
            f"<td>{_money(_agg_unit)}</td>"
            f"<td>{_money(_agg_care)}</td>"
            f"<td>{_money(_agg_total)}</td>"
            f"<td>{_money(_agg_care_total)}</td>"
            f"<td>{_he(_agg_reason)}</td>"
            "</tr>"
        )

    def _catalog_table(category: str, title: str) -> str:
        rows = []
        if not isinstance(_PRICE_PRODUCTS, dict):
            return ""
        for key, product in sorted(_PRICE_PRODUCTS.items(), key=lambda item: (str((item[1] or {}).get("category")), str((item[1] or {}).get("name")))):
            if not isinstance(product, dict) or product.get("category") != category:
                continue
            care = _product_care_cost(product)
            cyber = _product_cyber_cost(product)
            adders = []
            if isinstance(care, int):
                adders.append(f"UI Care 5-year {_money(care)}")
            if isinstance(cyber, int):
                adders.append(f"CyberSecure annual {_money(cyber)}")
            rows.append(
                "<tr>"
                f"<td>{_he(product.get('name') or key)}<br><code>{_he(product.get('sku') or key)}</code></td>"
                f"<td>{_money(_product_unit_cost(product))}</td>"
                f"<td>{_price_confidence_badge(_product_price_confidence(product))}</td>"
                f"<td>{_he(' · '.join(adders) or '—')}</td>"
                f"<td>{_he(product.get('description') or '')}</td>"
                f"<td>{_product_source(product)}</td>"
                "</tr>"
            )
        if not rows:
            return ""
        return f"""
        <h4>{_he(title)}</h4>
        <table class="data dense">
          <thead><tr><th>Product</th><th>Unit</th><th>Confidence</th><th>Support / Services</th><th>Planning Notes</th><th>Source</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        """

    _reference_catalog_html = (
        _catalog_table("meraki_used", "Cisco/Meraki Used-Market Reference")
        + _catalog_table("access_point", "Access Point Reference")
        + _catalog_table("switch", "Access Switch Reference")
        + _catalog_table("aggregation", "Aggregation Reference")
        + _catalog_table("gateway", "Gateway Reference")
    )
    _unifi_grand_total = _unifi_total + _aggregation_total
    _unifi_grand_care_total = _unifi_care_total + _aggregation_care_total

    def _phase_amount(*categories: str, field: str = "hardware") -> int:
        return sum((_category_totals.get(category) or {}).get(field, 0) for category in categories)

    _year1_hw = _phase_amount("access_point")
    _year1_care = _phase_amount("access_point", field="care")
    _year2_hw = _phase_amount("switch", "aggregation")
    _year2_care = _phase_amount("switch", "aggregation", field="care")
    _year3_hw = _phase_amount("gateway")
    _year3_care = _phase_amount("gateway", field="care")
    _year3_cyber = _phase_amount("gateway", field="cyber")
    _cost_breakdown_rows = (
        "<tr>"
        "<td>Wireless AP hardware</td>"
        f"<td>{_money(_year1_hw) if _year1_hw else '—'}</td>"
        f"<td>{_price_confidence_badge('Public MSRP') if _year1_hw else _price_confidence_badge('Quote needed')}</td>"
        "<td>Mapped active/alerting APs only; excludes dormant/offline APs until field validation.</td>"
        "</tr>"
        "<tr>"
        "<td>Access switch hardware</td>"
        f"<td>{_money(_phase_amount('switch')) if _phase_amount('switch') else '—'}</td>"
        f"<td>{_price_confidence_badge('Public MSRP') if _phase_amount('switch') else _price_confidence_badge('Quote needed')}</td>"
        "<td>Mapped active/alerting access switches; PoE and uplink design should be validated closet by closet.</td>"
        "</tr>"
        "<tr>"
        "<td>Aggregation hardware</td>"
        f"<td>{_money(_aggregation_total) if _aggregation_total else '—'}</td>"
        f"<td>{_price_confidence_badge('Public MSRP') if _aggregation_total else _price_confidence_badge('Quote needed')}</td>"
        "<td>Included only when connected SFP/module usage suggests a main closet aggregation candidate.</td>"
        "</tr>"
        "<tr>"
        "<td>Gateway/security hardware</td>"
        f"<td>{_money(_year3_hw) if _year3_hw else '—'}</td>"
        f"<td>{_price_confidence_badge('Public MSRP') if _year3_hw else _price_confidence_badge('Quote needed')}</td>"
        "<td>MX replacement is a planning placeholder until firewall, VPN, filtering, logging, and HA requirements are signed off.</td>"
        "</tr>"
        "<tr>"
        "<td>Optional support/services add-ons</td>"
        f"<td>{_money(_unifi_grand_care_total + _unifi_cyber_annual_total) if (_unifi_grand_care_total + _unifi_cyber_annual_total) else '—'}</td>"
        f"<td>{_price_confidence_badge('Public MSRP') if (_unifi_grand_care_total + _unifi_cyber_annual_total) else _price_confidence_badge('Quote needed')}</td>"
        "<td>UI Care and CyberSecure are shown separately from hardware so support choices stay explicit.</td>"
        "</tr>"
        "<tr>"
        "<td>Not included</td>"
        "<td>Pricing needed</td>"
        f"<td>{_price_confidence_badge('Quote needed')}</td>"
        "<td>Optics/transceivers, cabling, licensing renewal deltas, tax, freight, professional services, project contingency, and E-rate/reseller discounts.</td>"
        "</tr>"
    )
    _three_year_rows = (
        "<tr>"
        "<td>Year 1</td><td>Wireless access refresh</td>"
        f"<td>{_money(_year1_hw) if _year1_hw else '—'}</td>"
        f"<td>{_money(_year1_care) if _year1_care else '—'}</td>"
        "<td>Replace active APs first; leave dormant/offline APs out of the quote until validated.</td>"
        "</tr>"
        "<tr>"
        "<td>Year 2</td><td>Access switching and aggregation</td>"
        f"<td>{_money(_year2_hw) if _year2_hw else '—'}</td>"
        f"<td>{_money(_year2_care) if _year2_care else '—'}</td>"
        "<td>Move closets in controlled batches; include aggregation only when connected SFP/module use warrants it.</td>"
        "</tr>"
        "<tr>"
        "<td>Year 3</td><td>Gateway/security migration and cleanup</td>"
        f"<td>{_money(_year3_hw) if _year3_hw else '—'}</td>"
        f"<td>{_money(_year3_care + _year3_cyber) if (_year3_care + _year3_cyber) else '—'}</td>"
        "<td>Validate firewall, VPN, content filtering, logging, and security subscriptions before replacing MX edge services.</td>"
        "</tr>"
    )

    if _unifi_rows:
        _footer_meraki = _money(_meraki_total) if _priced_rows else "Pricing needed"
        _footer_unifi = _money(_unifi_total) if _unifi_priced_rows else "Pricing needed"
        _footer_delta = f"-{_savings_pct}%" if isinstance(_savings_pct, int) else "Pricing needed"
        _unifi_hw_table = f"""
        <table class="data dense">
          <thead>
            <tr>
              <th>Meraki Model</th><th>Inventory Qty</th><th>Quoted Qty</th><th>Excluded</th><th>UniFi Equivalent</th>
              <th>Meraki Unit</th><th>Meraki Source</th><th>UniFi Unit</th><th>UI Care / Unit</th>
              <th>Meraki Total</th><th>UniFi Total</th><th>UI Care Total</th><th>Flag</th>
            </tr>
          </thead>
          <tbody>{_unifi_rows}</tbody>
          <tfoot>
            <tr>
              <td colspan="9"><strong>Hardware totals (active/alerting mapped rows only)</strong></td>
              <td><strong>{_footer_meraki}</strong></td>
              <td><strong>{_footer_unifi}</strong></td>
              <td><strong>{_money(_unifi_care_total) if _unifi_care_total else "—"}</strong></td>
              <td><strong>{_footer_delta}</strong></td>
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
      <p>This section maps current Meraki model families to UniFi replacement classes and
         builds a first-pass migration bill of materials. It is a planning reference only,
         not a procurement quote or a recommendation to replace. Built-in UniFi prices use
         the maintained <code>reporting/reference/pricing_reference.json</code> catalog.
         Cisco/Meraki prices are shown only when an explicit reference exists; NetworkTigers
         entries are labeled <strong>NetworkTigers (used)</strong> because they are used-market
         hardware references and exclude licensing, warranty, support, tax, freight, optics,
         and implementation;
         org-local <code>pricing.json</code> overrides should be used for reseller, E-rate,
         or client-approved pricing.</p>

      <div class="summary-card">
        <div class="summary-title">Planning Summary</div>
        <div class="summary-body">
          Mapped model families: <strong>{_mapped_rows}</strong>
          · Inventory devices considered: <strong>{_inventory_refresh_qty}</strong>
          · Active/alerting devices found: <strong>{_quoted_refresh_qty}</strong>
          · Quoted mapped devices: <strong>{_mapped_quoted_qty}</strong>
          · Excluded dormant/offline/unknown devices: <strong>{_excluded_refresh_qty}</strong>
          · Excluded status mix: <strong>{_excluded_status_summary}</strong>
          · UniFi priced rows: <strong>{_unifi_priced_rows}</strong>
          · Reference updated: <strong>{_he(_PRICE_UPDATED)}</strong>
          · Meraki hardware total: <strong>{_money(_meraki_total) if _priced_rows else "Pricing needed"}</strong>
          · UniFi mapped hardware total: <strong>{_money(_unifi_total) if _unifi_priced_rows else "Pricing needed"}</strong>
          · Optional aggregation hardware: <strong>{_money(_aggregation_total) if _aggregation_total else "—"}</strong>
          · UniFi planning total: <strong>{_money(_unifi_grand_total) if _unifi_grand_total else "Pricing needed"}</strong>
          · UI Care 5-year add-on: <strong>{_money(_unifi_grand_care_total) if _unifi_grand_care_total else "—"}</strong>
          · CyberSecure annual add-on: <strong>{_money(_unifi_cyber_annual_total) if _unifi_cyber_annual_total else "—"}</strong>
          · Hardware delta: <strong>{_money(_savings) + f" ({_savings_pct}% lower)" if _is_number(_savings) and isinstance(_savings_pct, int) else "Pricing needed"}</strong>
          {f"· EOL mapped families: <strong>{_he(', '.join(_eol_models_mapped[:6]))}</strong>" if _eol_models_mapped else ""}
          <br><br>
          <em>Currency: {_he(_PRICE_CURRENCY)}. Meraki pricing remains quote-dependent unless supplied
          by org-local <code>pricing.json</code>. Validate all pricing, support terms, tax, freight,
          optics, and professional services before using externally.</em>
        </div>
      </div>

      {_unifi_hw_table}

      <h3>Migration Cost Breakdown</h3>
      <table class="data dense">
        <thead>
          <tr><th>Cost Area</th><th>Planning Amount</th><th>Confidence</th><th>Notes</th></tr>
        </thead>
        <tbody>{_cost_breakdown_rows}</tbody>
      </table>

      <h3>Three-Year Migration Budget View</h3>
      <table class="data dense">
        <thead>
          <tr><th>Phase</th><th>Scope</th><th>Hardware</th><th>Support / Services Add-ons</th><th>Planning Notes</th></tr>
        </thead>
        <tbody>{_three_year_rows}</tbody>
        <tfoot>
          <tr>
            <td colspan="2"><strong>Three-year planning total</strong></td>
            <td><strong>{_money(_unifi_grand_total) if _unifi_grand_total else "Pricing needed"}</strong></td>
            <td><strong>{_money(_unifi_grand_care_total + _unifi_cyber_annual_total) if (_unifi_grand_care_total + _unifi_cyber_annual_total) else "—"}</strong></td>
            <td><strong>{_money(_unifi_grand_total + _unifi_grand_care_total + _unifi_cyber_annual_total) if _unifi_grand_total else "Pricing needed"}</strong></td>
          </tr>
        </tfoot>
      </table>

      <h3>Aggregation / Main Closet Reference</h3>
      <div class="summary-card">
        <div class="summary-title">Observed SFP Footprint</div>
        <div class="summary-body">
          SFP/module ports observed: <strong>{_sfp_total}</strong>
          · Connected SFP/module ports: <strong>{_connected_sfp}</strong>
          · Uplink SFP/module ports: <strong>{_uplink_sfp}</strong>
          <br>
          {_he(_agg_reason)}
        </div>
      </div>
      <table class="data dense">
        <thead>
          <tr><th>Candidate</th><th>Qty</th><th>Unit</th><th>UI Care / Unit</th><th>Total</th><th>UI Care Total</th><th>Reason</th></tr>
        </thead>
        <tbody>{_aggregation_rows or '<tr><td colspan="7" class="empty-state">No aggregation switch add-on suggested from observed SFP usage.</td></tr>'}</tbody>
      </table>

      <h3>Maintained UniFi Public Reference Catalog</h3>
      <p>The catalog below is kept in source control so migration calculations are repeatable
         and auditable. It should be refreshed before client-facing procurement decisions.</p>
      {_reference_catalog_html}

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
            <td>No per-device network-device license fees. Hardware purchased once.
                Optional cloud services should be priced from current Ubiquiti terms.</td>
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

    vlan_reference_rows = [
        ("1", "Native / Management", "10.1.0.0/16", "Switch, MX, AP management; IT jump hosts", "IT-only management access; no user assignment"),
        ("10", "Servers & Controllers", "10.10.0.0/16", "SIS, NVR, file shares, local controllers", "Allow approved staff/admin sources to specific services only"),
        ("20", "Facilities / IoT", "10.20.0.0/16", "HVAC, PA, alarms, signage", "Outbound vendor/NTP/DNS only; block inbound and lateral movement"),
        ("30", "Security Devices", "10.30.0.0/16", "Cameras and door access panels", "Permit NVR/control-plane flows; block general internet and user VLAN access"),
        ("100", "Admin Staff", "10.100.0.0/16", "Admin SSID and office workstations", "Least-privilege LAN access; deny student and guest networks"),
        ("110-180", "Teacher / Classroom Blocks", "10.110.0.0/16 - 10.180.0.0/16", "Teacher devices and classroom carts by building or role", "Local print/cast/mDNS where required; restrict server access to approved applications"),
        ("200", "Voice / Collaboration", "10.200.0.0/16", "VoIP phones, PA speakers, room systems", "SIP/RTP to call control only; preserve EF/voice QoS"),
        ("250", "Student / BYOD", "10.250.0.0/16", "Student SSID and unmanaged student devices", "Internet-only with content filtering; no internal LAN access"),
        ("254", "Guest / Visitor", "10.254.0.0/16", "Guest SSID and captive portal users", "Internet-only; captive portal and rate limits"),
        ("400", "Events / Special Use", "10.400.0.0/16", "Athletics, auditorium AV, temporary wireless", "Time-bound policy; block production VLANs except explicitly approved multicast"),
    ]
    vlan_reference_html = """
    <section id="vlan-reference" class="report-section">
      <h2>18. K-12 VLAN Segmentation Reference</h2>
      <p>This supplemental design is a reference blueprint for school network segmentation. It should be validated against the current Meraki Dashboard configuration, firewall policy, identity provider, print/casting needs, and building-by-building operational requirements before implementation.</p>
      <table class="data dense">
        <thead>
          <tr><th>VLAN</th><th>Name / Purpose</th><th>Reference Subnet</th><th>Typical Devices</th><th>Policy Intent</th></tr>
        </thead>
        <tbody>
    """ + "".join(
        "<tr>"
        f"<td>{_he(vlan)}</td>"
        f"<td>{_he(name)}</td>"
        f"<td><code>{_he(subnet)}</code></td>"
        f"<td>{_he(devices)}</td>"
        f"<td>{_he(policy)}</td>"
        "</tr>"
        for vlan, name, subnet, devices, policy in vlan_reference_rows
    ) + """
        </tbody>
      </table>
      <div class="summary-card">
        <div class="summary-title">Dashboard Implementation Notes</div>
        <div class="summary-body">
          Map SSIDs to tagged VLANs, keep management unassigned to users, apply deny-by-default inter-VLAN firewall rules, and use group policies for guest, student, IoT, and event exceptions. Treat this as target architecture, not evidence of current compliance.
        </div>
      </div>
    </section>
    """

    end_report_html = f"""
    <section class="report-section end-report">
      <div>
        <h2>End of Report</h2>
        <p>TM Meraki Baseline</p>
        <p>Release {REPORT_VERSION} &nbsp;&bull;&nbsp; Generated {_report_ts}</p>
        <p>{_he(org_name)}</p>
      </div>
    </section>
    """

    full_body = (
        cover_html
        + _schema_banner
        + toc_html
        + exec_html
        + report_guide_html
        + network_overview_html
        + topology_html
        + traffic_html
        + issues_html
        + poe_html
        + ups_html
        + security_html
        + recommendations_html
        + cis8_html
        + licensing_html
        + config_coverage_html
        + budget_forecast_html
        + wan_capacity_html
        + ap_interference_html
        + client_analysis_html
        + switch_main_report_html
        + unifi_html
        + vlan_reference_html
        + end_report_html
    )
    exec_body = cover_html + _schema_banner + exec_html + report_guide_html + end_report_html
    ap_spectrum_body = cover_html + _schema_banner + ap_spectrum_html + end_report_html
    battery_body = cover_html + _schema_banner + ups_html + end_report_html
    backup_body = (
        cover_html
        + _schema_banner
        + toc_backup_html
        + backup_intro_html
        + config_coverage_html
        + network_overview_html
        + security_html
        + licensing_html
        + client_analysis_html
        + switch_deep_dive_html
        + end_report_html
    )

    if report_kind == "exec":
        return exec_body
    if report_kind in {"battery_backup", "battery-backup", "battery", "ups", "ups_runtime"}:
        return battery_body
    if report_kind in {"ap_spectrum", "ap-spectrum", "ap_interference"}:
        return ap_spectrum_body
    if report_kind == "backup":
        return backup_body
    return full_body

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Meraki HTML/PDF reports.")
    parser.add_argument("--source-dir", help="Generate reports from a single backup/fixture directory.")
    parser.add_argument("--org-name", help="Display name for --source-dir reports.")
    parser.add_argument("--output-dir", help="Directory for generated reports when using --source-dir.")
    parser.add_argument(
        "--reports-dir",
        help=(
            "Write multi-org report output under this reports directory instead of inside backups/. "
            "Each org gets reports/<org>/<timestamp>/ plus reports/latest/<org>/ aliases."
        ),
    )
    parser.add_argument(
        "--pdf-only",
        action="store_true",
        help="Remove generated HTML artifacts after PDF rendering succeeds.",
    )
    parser.add_argument(
        "--fixed-now",
        type=_validate_fixed_now,
        help="Use a fixed ISO timestamp for deterministic report filenames and visible report dates.",
    )
    args = parser.parse_args(argv)

    if args.fixed_now:
        os.environ[FIXED_NOW_ENV] = args.fixed_now

    if args.source_dir:
        source_dir = os.path.abspath(args.source_dir)
        if not os.path.isdir(source_dir):
            log.error("Source directory not found: %s", source_dir)
            return 1
        org_name = args.org_name or _read_org_name(source_dir)
        output_dir = os.path.abspath(args.output_dir) if args.output_dir else None
        latest_dir = None
        if args.reports_dir and not output_dir:
            reports_dir = os.path.abspath(args.reports_dir)
            run_ts = _current_run_ts()
            output_dir = _report_run_output_dir(reports_dir, org_name, run_ts)
            latest_dir = _report_latest_output_dir(reports_dir, org_name)
        generated = generate_org_reports(
            source_dir,
            org_name,
            output_dir=output_dir,
            latest_dir=latest_dir,
            keep_html=not args.pdf_only,
            run_ts=run_ts if args.reports_dir and not args.output_dir else None,
        )
        log.info("Done — %d report(s) generated.", generated)
        return 0

    org_dirs = find_org_dirs(BACKUPS_DIR)
    if not org_dirs:
        log.error("No org directories found in %s/", BACKUPS_DIR)
        log.error("Run meraki_backup.py first.")
        return 1

    generated = 0
    for org_dir in org_dirs:
        org_name = _read_org_name(org_dir)
        output_dir = None
        latest_dir = None
        if args.reports_dir:
            reports_dir = os.path.abspath(args.reports_dir)
            run_ts = _current_run_ts()
            output_dir = _report_run_output_dir(reports_dir, org_name, run_ts)
            latest_dir = _report_latest_output_dir(reports_dir, org_name)
        generated += generate_org_reports(
            org_dir,
            org_name,
            output_dir=output_dir,
            latest_dir=latest_dir,
            keep_html=not args.pdf_only,
            run_ts=run_ts if args.reports_dir else None,
        )

    log.info("Done — %d report(s) generated.", generated)
    return 0
