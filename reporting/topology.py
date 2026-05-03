import re
from collections import deque
from typing import Any, Dict, List, Tuple

# ── Network topology SVG ─────────────────────────────────────────────────────
_TOPO_NW        = 132    # node width px
_TOPO_NH        = 46     # node height px  (internet / default)
_TOPO_NH_MX     = 150    # appliance card height px
_TOPO_NH_SW     = 130    # switch card height px
_TOPO_HG        = 14     # horizontal gap between siblings
_TOPO_VG        = 72     # vertical gap between layers
_TOPO_PX        = 36     # left/right padding
_TOPO_PY        = 28     # top/bottom padding
_TOPO_MAX       = 8      # max real nodes per layer; excess → stub
_TOPO_SPLIT_THR = 8      # max switches in any layer before per-branch pagination

_TOPO_C: Dict[str, Dict[str, str]] = {
    "internet":  {"bg": "#1c1917", "fg": "#c4c9b0", "bd": "#44403c"},
    "appliance": {"bg": "#3b3e2d", "fg": "#eef0e6", "bd": "#6e754b"},
    "switch":    {"bg": "#575d3d", "fg": "#eef0e6", "bd": "#8a9269"},
    "wireless":  {"bg": "#8a9269", "fg": "#1f2117", "bd": "#6e754b"},
    "camera":    {"bg": "#44403c", "fg": "#f5f5f4", "bd": "#78716c"},
    "sensor":    {"bg": "#44403c", "fg": "#f5f5f4", "bd": "#78716c"},
}
_TOPO_DOT: Dict[str, str] = {
    "online": "#4ade80", "offline": "#f87171",
    "alerting": "#fb923c", "dormant": "#94a3b8",
}
_TOPO_BADGE: Dict[str, str] = {
    "appliance": "MX", "switch": "MS", "wireless": "MR",
    "camera": "MV", "sensor": "MT", "internet": "WAN",
}


def _svg_esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _card_h(ntype: str) -> int:
    """Return card height in px for a given device type."""
    return {"appliance": _TOPO_NH_MX, "switch": _TOPO_NH_SW}.get(ntype, _TOPO_NH)


# ── Enrichment helpers ────────────────────────────────────────────────────────

def _mx_wan_lines(serial: str, uplink_statuses: List) -> List[str]:
    """Return display strings for each WAN interface on an MX (public IP + status)."""
    lines = []
    for entry in (uplink_statuses if isinstance(uplink_statuses, list) else []):
        if entry.get("serial") != serial:
            continue
        for ul in entry.get("uplinks", []):
            iface = ul.get("interface", "").upper()
            pub = ul.get("publicIp") or ul.get("ip") or ""
            status = ul.get("status", "")
            suffix = "" if status == "active" else f" ({status})"
            if pub:
                lines.append(f"{iface}: {pub}{suffix}")
    return lines[:2]


def _mx_peak_gbps(network_id: str, uplinks_usage: Any) -> Dict[str, float]:
    """Return {"wan1": peak_GB, "wan2": peak_GB} from hourly usage history."""
    entries = uplinks_usage.get(network_id, []) if isinstance(uplinks_usage, dict) else []
    peak: Dict[str, float] = {}
    for entry in (entries if isinstance(entries, list) else []):
        for iface in entry.get("byInterface", []):
            name = (iface.get("interface") or "").lower()
            gb = (iface.get("sent", 0) + iface.get("received", 0)) / 1e9
            if gb > peak.get(name, 0.0):
                peak[name] = gb
    return peak


def _switch_vlans(serial: str, port_configs_by_switch: Dict) -> str:
    """Compact VLAN summary for a switch from its port configs."""
    ports = port_configs_by_switch.get(serial, [])
    vlans: set = set()
    for p in (ports if isinstance(ports, list) else []):
        if not isinstance(p, dict):
            continue
        if p.get("type") == "access":
            v = p.get("vlan")
            if v and str(v).isdigit():
                vlans.add(int(v))
        elif p.get("type") == "trunk":
            for part in str(p.get("allowedVlans") or "").split(","):
                part = part.strip()
                if "-" in part:
                    try:
                        a, b = part.split("-", 1)
                        vlans.update(range(int(a), min(int(b) + 1, int(a) + 64)))
                    except ValueError:
                        pass
                elif part.isdigit():
                    vlans.add(int(part))
    if not vlans:
        return ""
    s = sorted(vlans)
    if len(s) > 8:
        return f"{len(s)} VLANs"
    return "VLANs: " + ", ".join(str(v) for v in s)


def _switch_client_count(serial: str, port_statuses_by_switch: Dict) -> int:
    """Sum clientCount across all ports on a switch."""
    ports = port_statuses_by_switch.get(serial, {})
    if isinstance(ports, list):
        return sum(p.get("clientCount", 0) for p in ports if isinstance(p, dict))
    if isinstance(ports, dict):
        return sum(p.get("clientCount", 0) for p in ports.values() if isinstance(p, dict))
    return 0


def _switch_poe_str(serial: str, port_statuses_by_switch: Dict) -> str:
    """Average PoE draw in watts over the 24h collection window, or ''."""
    ports = port_statuses_by_switch.get(serial, {})
    port_list = ports if isinstance(ports, list) else list(ports.values()) if isinstance(ports, dict) else []
    total_wh = sum(p.get("powerUsageInWh", 0) for p in port_list if isinstance(p, dict))
    avg_w = total_wh / 24.0
    return f"{avg_w:.0f}W PoE" if avg_w >= 1 else ""


# ── Pagination helper ─────────────────────────────────────────────────────────

def _topo_pages(
    devices: List[Dict],
    lldp_cdp: Dict,
    ap_util: Dict,
    port_issues: Dict,
    switch_port_statuses_by_switch: Dict[str, Any],
    enrichment: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """
    Return a list of {"title": str, "svg": str} dicts — one per diagram page.

    Small sites (≤ _TOPO_SPLIT_THR switches in any layer) return a single entry.
    Large sites (WPC-style campuses) return an overview + one entry per tier-2 branch
    so each diagram fits comfortably on a PDF page.
    """
    if not devices:
        return [{"title": "", "svg": ""}]

    # ── Build adjacency (shared logic, duplicated minimally here) ────────────
    def _norm(v: Any) -> str:
        return "".join(c for c in str(v).lower() if c.isalnum())

    s2d: Dict[str, Dict] = {d["serial"]: d for d in devices if d.get("serial")}
    id2s: Dict[str, str] = {}
    for serial, dev in s2d.items():
        id2s[_norm(serial)] = serial
        if dev.get("mac"):
            id2s[_norm(dev["mac"])] = serial

    sw_status: Dict[str, Dict[str, Dict]] = {}
    if isinstance(switch_port_statuses_by_switch, dict):
        for serial, ports in switch_port_statuses_by_switch.items():
            if isinstance(ports, list):
                sw_status[serial] = {str(p.get("portId")): p for p in ports if isinstance(p, dict)}

    def _upstream_rank(serial: str) -> int:
        dev = s2d[serial]
        if dev.get("productType") == "appliance":
            return 1000
        ports = sw_status.get(serial, {})
        return (400
                + sum(20 for p in ports.values() if "10 Gbps" in str(p.get("speed") or ""))
                + sum(5 for p in ports.values() if p.get("isUplink"))
                + len(ports))

    links: List[Dict] = []
    seen: set = set()
    for local_s, data in (lldp_cdp.items() if isinstance(lldp_cdp, dict) else []):
        if local_s not in s2d or not isinstance(data, dict):
            continue
        ports = data.get("ports", {})
        if not isinstance(ports, dict):
            continue
        for local_port, pd in ports.items():
            if not isinstance(pd, dict):
                continue
            for disc in [pd.get("lldp"), pd.get("cdp")]:
                if not isinstance(disc, dict):
                    continue
                tgt = None
                for cand in (disc.get("chassisId"), disc.get("deviceId"), pd.get("deviceMac")):
                    tgt = id2s.get(_norm(cand))
                    if tgt:
                        break
                if not tgt or tgt == local_s:
                    continue
                key = (local_s, str(local_port), tgt)
                if key in seen:
                    continue
                seen.add(key)
                lst = sw_status.get(local_s, {}).get(str(local_port), {})
                links.append({"local": local_s, "local_port": str(local_port),
                               "local_is_uplink": bool(lst.get("isUplink")),
                               "local_speed": str(lst.get("speed") or ""),
                               "remote": tgt,
                               "remote_port": str(disc.get("portId") or "")})

    children: Dict[str, List[str]] = {}
    parent_of: Dict[str, str] = {}
    cands: Dict[str, List] = {}
    for lnk in links:
        lt = s2d[lnk["local"]].get("productType")
        rt = s2d[lnk["remote"]].get("productType")
        child = parent = ""
        score = 0
        if lnk["local_is_uplink"] and rt in ("switch", "appliance"):
            child, parent, score = lnk["local"], lnk["remote"], 100
        elif lt == "appliance" and rt != "appliance":
            child, parent, score = lnk["remote"], lnk["local"], 90
        elif rt == "appliance" and lt != "appliance":
            child, parent, score = lnk["local"], lnk["remote"], 90
        elif lt == "switch" and rt in ("wireless", "camera", "sensor"):
            child, parent, score = lnk["remote"], lnk["local"], 80
        elif rt == "switch" and lt in ("wireless", "camera", "sensor"):
            child, parent, score = lnk["local"], lnk["remote"], 80
        elif lt == "switch" and rt == "switch":
            if _upstream_rank(lnk["local"]) >= _upstream_rank(lnk["remote"]):
                child, parent, score = lnk["remote"], lnk["local"], 60
            else:
                child, parent, score = lnk["local"], lnk["remote"], 60
        if child and parent and child != parent:
            cands.setdefault(child, []).append((score, parent))

    for child, options in cands.items():
        options.sort(key=lambda x: (-x[0], -_upstream_rank(x[1])))
        parent = options[0][1]
        parent_of[child] = parent
        children.setdefault(parent, []).append(child)

    roots = [s for s, d in s2d.items() if d.get("productType") == "appliance" and s not in parent_of]
    if not roots:
        roots = [s for s, d in s2d.items() if d.get("productType") == "switch" and s not in parent_of]
    if not roots:
        roots = [next(iter(s2d))]

    depth: Dict[str, int] = {}

    def _assign(serial: str, val: int) -> None:
        if serial in depth and depth[serial] <= val:
            return
        depth[serial] = val
        for c in children.get(serial, []):
            _assign(c, val + 1)

    for r in roots:
        _assign(r, 1)
    _fallback = {"appliance": 1, "switch": 2, "wireless": 3, "camera": 3, "sensor": 4}
    for s, d in s2d.items():
        if s not in depth:
            depth[s] = _fallback.get(d.get("productType", ""), 3)

    sw_serials = [s for s, d in s2d.items()
                  if d.get("productType") in ("appliance", "switch")]
    by_depth: Dict[int, List[str]] = {}
    for s in sw_serials:
        by_depth.setdefault(depth[s], []).append(s)

    max_layer = max((len(v) for v in by_depth.values()), default=0)

    # ── Small site — single diagram ──────────────────────────────────────────
    if max_layer <= _TOPO_SPLIT_THR:
        return [{"title": "", "svg": _topo_svg(
            devices, lldp_cdp, ap_util, port_issues,
            switch_port_statuses_by_switch, enrichment=enrichment,
        )}]

    # ── Large site — overview + per-branch detail ────────────────────────────
    # A true branch root is any switch whose confirmed parent is NOT another
    # switch in this site — covers:
    #   • roots with no LLDP parent at all
    #   • switches whose upstream LLDP resolves to the MX serial
    #   • switches whose upstream LLDP resolves to the MX via CDP name/MAC
    #     (e.g. "Firewall-6c:7f:0c" — the MX isn't in sw_serials either way)
    # This is more reliable than depth==2, which gets polluted by fallback values.
    sw_serials = {s for s, d in s2d.items() if d.get("productType") == "switch"}
    tier2 = [s for s in sw_serials if parent_of.get(s) not in sw_serials]

    pages: List[Dict[str, str]] = []

    # Overview: MX + all tier-2 switches only
    overview_devs = [d for d in devices
                     if d.get("productType") == "appliance"
                     or d["serial"] in tier2]
    pages.append({"title": "Overview — Core / Distribution Layer",
                  "svg": _topo_svg(
                      overview_devs, lldp_cdp, ap_util, port_issues,
                      switch_port_statuses_by_switch, enrichment=enrichment,
                  )})

    # Per-branch detail: tier-2 switch + its full subtree + parent MX stub
    type_order = {"appliance": 0, "switch": 1, "wireless": 2, "camera": 3, "sensor": 4}
    tier2_sorted = sorted(tier2, key=lambda s: (
        type_order.get(s2d[s].get("productType", ""), 9),
        s2d[s].get("name") or s,
    ))

    for t2 in tier2_sorted:
        subtree: set = set()
        q: deque = deque([t2])
        while q:
            node = q.popleft()
            if node in subtree:
                continue
            subtree.add(node)
            for c in children.get(node, []):
                q.append(c)
        # Branch diagram: tier-2 switch + its subtree only.
        # No Internet node, no MX stub — the tier-2 switch is the root.
        # Its card already shows "Above: MX_NAME" as context.
        branch_devs = [d for d in devices if d.get("serial") in subtree]
        branch_name = s2d[t2].get("name") or s2d[t2].get("model") or t2
        pages.append({"title": branch_name,
                      "svg": _topo_svg(
                          branch_devs, lldp_cdp, ap_util, port_issues,
                          switch_port_statuses_by_switch, enrichment=enrichment,
                          show_internet=False,
                      )})

    return pages


def _topo_svg(
    devices: List[Dict],
    lldp_cdp: Dict,
    ap_util: Dict,
    port_issues: Dict,
    switch_port_statuses_by_switch: Dict[str, Any],
    enrichment: Optional[Dict] = None,
    show_internet: bool = True,
) -> str:
    """Return an inline SVG topology using parent/child relationships from ports."""
    if not devices:
        return ""

    enr = enrichment or {}
    node_w = 196
    node_h = 108        # internet / default card height
    layer_gap = 104
    col_gap = 20
    pad_x = 28
    pad_y = 26

    def _norm(value: Any) -> str:
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    def _port_sort_key(value: Any) -> Tuple[int, str]:
        text = str(value)
        m = re.match(r"(\d+)", text)
        if m:
            return (0, f"{int(m.group(1)):05d}{text}")
        return (1, text)

    serial_to_dev: Dict[str, Dict[str, Any]] = {
        d["serial"]: d for d in devices if d.get("serial")
    }
    if not serial_to_dev:
        return ""

    id_to_serial: Dict[str, str] = {}
    for serial, dev in serial_to_dev.items():
        id_to_serial[_norm(serial)] = serial
        if dev.get("mac"):
            id_to_serial[_norm(dev.get("mac"))] = serial

    status_by_switch: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if isinstance(switch_port_statuses_by_switch, dict):
        for serial, ports in switch_port_statuses_by_switch.items():
            if isinstance(ports, list):
                status_by_switch[serial] = {
                    str(p.get("portId")): p for p in ports if isinstance(p, dict)
                }

    links: List[Dict[str, Any]] = []
    link_seen: set = set()
    for local_serial, data in lldp_cdp.items() if isinstance(lldp_cdp, dict) else []:
        if local_serial not in serial_to_dev or not isinstance(data, dict):
            continue
        ports = data.get("ports", {})
        if not isinstance(ports, dict):
            continue
        for local_port, port_data in ports.items():
            if not isinstance(port_data, dict):
                continue
            candidates = []
            for key in ("lldp", "cdp"):
                disc = port_data.get(key)
                if isinstance(disc, dict):
                    candidates.append(disc)
            for disc in candidates:
                target_serial = None
                for candidate in (
                    disc.get("chassisId"),
                    disc.get("deviceId"),
                    port_data.get("deviceMac"),
                ):
                    target_serial = id_to_serial.get(_norm(candidate))
                    if target_serial:
                        break
                if not target_serial or target_serial == local_serial:
                    continue
                dedupe = (local_serial, str(local_port), target_serial)
                if dedupe in link_seen:
                    continue
                link_seen.add(dedupe)
                local_status = status_by_switch.get(local_serial, {}).get(str(local_port), {})
                links.append(
                    {
                        "local": local_serial,
                        "local_port": str(local_port),
                        "local_is_uplink": bool(local_status.get("isUplink")),
                        "local_speed": str(local_status.get("speed") or ""),
                        "remote": target_serial,
                        "remote_port": str(disc.get("portId") or ""),
                        "system_name": disc.get("systemName") or "",
                        "confirmed": True,
                    }
                )

    node_info: Dict[str, Dict[str, Any]] = {}
    children: Dict[str, List[str]] = {}
    parent_of: Dict[str, str] = {}
    parent_link_of: Dict[str, Dict[str, Any]] = {}

    def _upstream_rank(serial: str) -> int:
        dev = serial_to_dev[serial]
        if dev.get("productType") == "appliance":
            return 1000
        ports = status_by_switch.get(serial, {})
        port_count = len(ports)
        uplinks = sum(1 for p in ports.values() if p.get("isUplink"))
        ten_g = sum(1 for p in ports.values() if "10 Gbps" in str(p.get("speed") or ""))
        return 400 + ten_g * 20 + uplinks * 5 + port_count

    for serial, dev in serial_to_dev.items():
        node_info[serial] = {
            "serial": serial,
            "type": dev.get("productType", "switch"),
            "status": dev.get("status", "unknown"),
            "label": (dev.get("name") or dev.get("model") or serial)[:22],
            "model": dev.get("model", ""),
            "ports": sorted(status_by_switch.get(serial, {}).values(), key=lambda p: _port_sort_key(p.get("portId"))),
        }

    candidate_parents: Dict[str, List[Tuple[int, str, Dict[str, Any]]]] = {}
    for link in links:
        left = serial_to_dev[link["local"]]
        right = serial_to_dev[link["remote"]]
        ltype = left.get("productType")
        rtype = right.get("productType")

        child = ""
        parent = ""
        score = 0
        if link["local_is_uplink"] and rtype in ("switch", "appliance"):
            child, parent, score = link["local"], link["remote"], 100
        elif ltype == "appliance" and rtype != "appliance":
            child, parent, score = link["remote"], link["local"], 90
        elif rtype == "appliance" and ltype != "appliance":
            child, parent, score = link["local"], link["remote"], 90
        elif ltype == "switch" and rtype in ("wireless", "camera", "sensor"):
            child, parent, score = link["remote"], link["local"], 80
        elif rtype == "switch" and ltype in ("wireless", "camera", "sensor"):
            child, parent, score = link["local"], link["remote"], 80
        elif ltype == "switch" and rtype == "switch":
            if _upstream_rank(link["local"]) >= _upstream_rank(link["remote"]):
                child, parent, score = link["remote"], link["local"], 60
            else:
                child, parent, score = link["local"], link["remote"], 60

        if child and parent and child != parent:
            candidate_parents.setdefault(child, []).append((score, parent, link))

    for child, options in candidate_parents.items():
        options.sort(key=lambda item: (-item[0], -_upstream_rank(item[1]), item[1]))
        _, parent, link = options[0]
        parent_of[child] = parent
        parent_link_of[child] = link
        children.setdefault(parent, []).append(child)

    roots = [
        serial for serial, dev in serial_to_dev.items()
        if dev.get("productType") == "appliance" and serial not in parent_of
    ]
    if not roots:
        roots = [
            serial for serial, dev in serial_to_dev.items()
            if dev.get("productType") == "switch" and serial not in parent_of
        ]
    if not roots:
        roots = [next(iter(serial_to_dev))]

    depth: Dict[str, int] = {}

    def _assign_depth(serial: str, value: int) -> None:
        if serial in depth and depth[serial] <= value:
            return
        depth[serial] = value
        for child in children.get(serial, []):
            _assign_depth(child, value + 1)

    for root in roots:
        _assign_depth(root, 1)
    for serial, dev in serial_to_dev.items():
        if serial not in depth:
            fallback = {"appliance": 1, "switch": 2, "wireless": 3, "camera": 3, "sensor": 4}
            depth[serial] = fallback.get(dev.get("productType"), 3)

    display_serials = [
        serial for serial, dev in serial_to_dev.items()
        if dev.get("productType") in ("appliance", "switch")
    ]
    if not display_serials:
        display_serials = list(serial_to_dev.keys())

    by_depth: Dict[int, List[str]] = {}
    type_order = {"appliance": 0, "switch": 1, "wireless": 2, "camera": 3, "sensor": 4}
    for serial, value in depth.items():
        if serial not in display_serials:
            continue
        by_depth.setdefault(value, []).append(serial)
    for serials in by_depth.values():
        serials.sort(key=lambda serial: (
            type_order.get(serial_to_dev[serial].get("productType", ""), 9),
            serial_to_dev[serial].get("name") or serial,
        ))

    layers: List[List[str]] = ([["__internet__"]] if show_internet else [])
    for value in sorted(by_depth):
        layers.append(by_depth[value])

    def _lh(layer: List[str]) -> int:
        """Max card height in a layer."""
        return max(
            (_card_h("internet") if s == "__internet__"
             else _card_h(serial_to_dev[s].get("productType", "")))
            for s in layer
        )

    max_cols = max(len(layer) for layer in layers)
    canvas_w = max_cols * (node_w + col_gap) - col_gap + 2 * pad_x
    positions: Dict[str, Tuple[float, float]] = {}
    layer_bottom: Dict[int, float] = {}

    cur_y = float(pad_y)
    for layer_index, layer in enumerate(layers):
        lh = _lh(layer)
        row_w = len(layer) * (node_w + col_gap) - col_gap
        x0 = (canvas_w - row_w) / 2
        for idx, serial in enumerate(layer):
            positions[serial] = (x0 + idx * (node_w + col_gap), cur_y)
        layer_bottom[layer_index] = cur_y + lh
        cur_y += lh + layer_gap

    canvas_h = cur_y - layer_gap + pad_y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" height="{canvas_h:.0f}" '
        f'viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}" '
        f'style="font-family:Inter,sans-serif;background:#f8fafc;border-radius:10px;'
        f'border:1px solid #e2e8f0;display:block;max-width:100%;">'
    ]

    def _ntype(serial: str) -> str:
        if serial == "__internet__":
            return "internet"
        return serial_to_dev.get(serial, {}).get("productType", "")

    parts.append('<g fill="none">')
    if show_internet and "__internet__" in positions:
        internet_x = positions["__internet__"][0] + node_w / 2
        internet_y = positions["__internet__"][1] + _card_h("internet")
        for root in roots:
            rx, ry = positions[root]
            parts.append(
                f'<line x1="{internet_x:.1f}" y1="{internet_y:.1f}" x2="{rx + node_w/2:.1f}" y2="{ry:.1f}" '
                f'stroke="#c4c9b0" stroke-width="1.6" stroke-dasharray="4 3" opacity="0.8"/>'
            )

    for child, parent in parent_of.items():
        if child not in display_serials or parent not in display_serials:
            continue
        px, py = positions[parent]
        cx, cy = positions[child]
        p_bot = py + _card_h(_ntype(parent))
        link = parent_link_of.get(child, {})
        speed = _svg_esc(link.get("local_speed") or "")
        local_port = _svg_esc(link.get("local_port") or "")
        remote_port = _svg_esc(link.get("remote_port") or "")
        parts.append(
            f'<line x1="{px + node_w/2:.1f}" y1="{p_bot:.1f}" x2="{cx + node_w/2:.1f}" y2="{cy:.1f}" '
            f'stroke="#8a9269" stroke-width="1.8" opacity="0.95"/>'
        )
        if speed or local_port or remote_port:
            mid_y = (p_bot + cy) / 2
            parts.append(
                f'<text x="{px + node_w/2:.1f}" y="{mid_y:.1f}" '
                f'text-anchor="middle" font-size="8" fill="#57534e" '
                f'paint-order="stroke" stroke="#f8fafc" stroke-width="3">'
                f'{_svg_esc(local_port)}'
                f'{" -> " + remote_port if remote_port else ""}'
                f'{" · " + speed if speed else ""}</text>'
            )
    parts.append("</g>")

    def _draw_switch_ports(nx: float, ny: float, ports: List[Dict[str, Any]]) -> str:
        if not ports:
            return ""
        panel_x = nx + 10
        panel_y = ny + 88   # shifted down for enrichment lines
        panel_w = node_w - 20
        panel_h = 26
        cols = min(max(len(ports) // 2, 12), 24)
        rows = 2 if len(ports) > cols else 1
        slot_w = (panel_w - (cols - 1) * 2) / cols
        slot_h = 10
        svg = [
            f'<rect x="{panel_x:.1f}" y="{panel_y:.1f}" width="{panel_w:.1f}" height="{panel_h:.1f}" '
            f'rx="5" fill="rgba(15,23,42,0.18)" stroke="rgba(255,255,255,0.18)" stroke-width="0.8"/>'
        ]
        for idx, port in enumerate(ports[: cols * rows]):
            row = idx // cols
            col = idx % cols
            x = panel_x + col * (slot_w + 2)
            y = panel_y + 4 + row * 12
            if port.get("isUplink"):
                fill = "#fbbf24"
            elif port.get("status") == "Connected":
                fill = "#4ade80"
            elif port.get("status") == "Disabled":
                fill = "#475569"
            else:
                fill = "#cbd5e1"
            svg.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot_w:.1f}" height="{slot_h:.1f}" '
                f'rx="1.8" fill="{fill}" opacity="0.95"/>'
            )
        return "".join(svg)

    internet_layer = ["__internet__"] if show_internet else []
    for serial in internet_layer + [s for layer in (layers[1:] if show_internet else layers) for s in layer]:
        nx, ny = positions[serial]
        nh = _card_h("internet") if serial == "__internet__" else _card_h(_ntype(serial))

        if serial == "__internet__":
            parts.append(
                f'<rect x="{nx:.1f}" y="{ny:.1f}" width="{node_w}" height="{nh}" rx="10" '
                f'fill="#1c1917" stroke="#44403c" stroke-width="1.2"/>'
            )
            parts.append(
                f'<text x="{nx + node_w/2:.1f}" y="{ny + nh/2 - 6:.1f}" text-anchor="middle" '
                f'font-size="20" font-weight="700" fill="#eef0e6">Internet</text>'
            )
            parts.append(
                f'<text x="{nx + node_w/2:.1f}" y="{ny + nh/2 + 12:.1f}" text-anchor="middle" '
                f'font-size="10" fill="#c4c9b0">Packet entry point</text>'
            )
            continue

        info = node_info[serial]
        ntype_s = info["type"]
        status = info["status"]
        label = _svg_esc(info["label"])
        model = _svg_esc(info["model"])
        C = _TOPO_C.get(ntype_s, _TOPO_C["camera"])
        dot_c = _TOPO_DOT.get(status, "#94a3b8")
        has_issue = bool(port_issues.get(serial))
        if ntype_s == "wireless":
            util = float((ap_util.get(serial) or {}).get("utilizationTotal", 0))
            has_issue = has_issue or util > 70
        border = "#f87171" if has_issue else C["bd"]
        border_w = "2" if has_issue else "1.2"

        parts.append(
            f'<rect x="{nx:.1f}" y="{ny:.1f}" width="{node_w}" height="{nh}" rx="10" '
            f'fill="{C["bg"]}" stroke="{border}" stroke-width="{border_w}"/>'
        )
        parts.append(
            f'<circle cx="{nx + node_w - 12:.1f}" cy="{ny + 12:.1f}" r="4" fill="{dot_c}"/>'
        )
        badge = _TOPO_BADGE.get(ntype_s, "")
        if badge:
            parts.append(
                f'<rect x="{nx+8:.1f}" y="{ny+8:.1f}" width="28" height="14" rx="3" fill="{C["bd"]}" opacity="0.78"/>'
            )
            parts.append(
                f'<text x="{nx+22:.1f}" y="{ny+18.5:.1f}" text-anchor="middle" font-size="8" font-weight="700" fill="{C["fg"]}">{badge}</text>'
            )

        parts.append(
            f'<text x="{nx + node_w/2:.1f}" y="{ny + 28:.1f}" text-anchor="middle" '
            f'font-size="11" font-weight="700" fill="{C["fg"]}">{label}</text>'
        )
        parts.append(
            f'<text x="{nx + node_w/2:.1f}" y="{ny + 42:.1f}" text-anchor="middle" '
            f'font-size="8.5" fill="{C["fg"]}" opacity="0.72">{model}</text>'
        )

        ports = info["ports"]

        if ntype_s == "appliance":
            # ── MX enrichment: WAN IPs, peak throughput, client count ──────
            wan_lines = _mx_wan_lines(serial, enr.get("uplink_statuses") or [])
            net_id = serial_to_dev[serial].get("networkId", "")
            peak = _mx_peak_gbps(net_id, enr.get("uplinks_usage") or {})
            client_total = (enr.get("clients_overview") or {}).get(net_id, {})
            client_count = (client_total.get("counts") or {}).get("total") if isinstance(client_total, dict) else None

            line_y = ny + 55
            for wl in wan_lines:
                parts.append(
                    f'<text x="{nx + 10:.1f}" y="{line_y:.1f}" font-size="8" fill="{C["fg"]}" opacity="0.9">{_svg_esc(wl)}</text>'
                )
                line_y += 13
            if peak:
                wan1 = peak.get("wan1", 0)
                wan2 = peak.get("wan2", 0)
                peak_str = f"WAN1 {wan1:.1f} GB" + (f"  WAN2 {wan2:.1f} GB" if wan2 > 0.1 else "") + " peak 7d"
                parts.append(
                    f'<text x="{nx + 10:.1f}" y="{line_y:.1f}" font-size="8" fill="{C["fg"]}" opacity="0.82">{_svg_esc(peak_str)}</text>'
                )
                line_y += 13
            if client_count is not None:
                parts.append(
                    f'<text x="{nx + 10:.1f}" y="{line_y:.1f}" font-size="8" fill="{C["fg"]}" opacity="0.82">{client_count:,} clients</text>'
                )

        else:
            # ── Switch enrichment: mgmt IP, VLANs, clients, PoE ──────────
            mgmt_ip = (enr.get("device_ip") or {}).get(serial, "")
            vlan_str = _switch_vlans(serial, enr.get("port_configs") or {})
            client_cnt = _switch_client_count(serial, switch_port_statuses_by_switch)
            poe_str = _switch_poe_str(serial, switch_port_statuses_by_switch)

            if mgmt_ip:
                parts.append(
                    f'<text x="{nx + 10:.1f}" y="{ny + 55:.1f}" font-size="8" fill="{C["fg"]}" opacity="0.88">{_svg_esc(mgmt_ip)}</text>'
                )
            if vlan_str:
                parts.append(
                    f'<text x="{nx + 10:.1f}" y="{ny + 67:.1f}" font-size="7.5" fill="{C["fg"]}" opacity="0.82">{_svg_esc(vlan_str[:40])}</text>'
                )
            stats = f"{client_cnt} clients" + (f"  ·  {poe_str}" if poe_str else "")
            parts.append(
                f'<text x="{nx + 10:.1f}" y="{ny + 79:.1f}" font-size="8" fill="{C["fg"]}" opacity="0.82">{_svg_esc(stats)}</text>'
            )
            parts.append(_draw_switch_ports(nx, ny, ports))

        if has_issue:
            parts.append(
                f'<text x="{nx + node_w - 20:.1f}" y="{ny + nh - 8:.1f}" font-size="10" fill="#fbbf24">&#9888;</text>'
            )

    parts.append("</svg>")
    return "".join(parts)


def _topo_summary_rows(
    devices: List[Dict[str, Any]],
    lldp_cdp: Dict[str, Any],
    switch_port_statuses_by_switch: Dict[str, Any],
) -> List[List[str]]:
    serial_to_dev, _, parent_of, children_of, edge_counts = _build_topology_facts(
        devices, lldp_cdp, switch_port_statuses_by_switch
    )
    if not serial_to_dev:
        return []

    rows: List[List[str]] = []
    for serial, dev in sorted(
        serial_to_dev.items(),
        key=lambda item: (
            0 if item[1].get("productType") == "appliance" else 1,
            item[1].get("name") or item[0],
        ),
    ):
        if dev.get("productType") not in ("appliance", "switch"):
            continue
        parent = parent_of.get(serial)
        if parent:
            parent_name = serial_to_dev.get(parent[0], {}).get("name") or parent[0]
            upstream = f"{parent_name} ({parent[1]} -> {parent[2] or '?'})"
        else:
            upstream = "Internet edge"
        rows.append(
            [
                dev.get("name") or serial,
                dev.get("model") or "",
                upstream,
                str(len(children_of.get(serial, []))),
                str(edge_counts.get(serial, 0)),
            ]
        )
    return rows


def _build_topology_facts(
    devices: List[Dict[str, Any]],
    lldp_cdp: Dict[str, Any],
    switch_port_statuses_by_switch: Dict[str, Any],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, Tuple[str, str, str]],
    Dict[str, List[str]],
    Dict[str, int],
]:
    serial_to_dev = {
        d.get("serial"): d for d in devices if isinstance(d, dict) and d.get("serial")
    }
    if not serial_to_dev:
        return {}, {}, {}, {}, {}

    def _norm(value: Any) -> str:
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    id_to_serial: Dict[str, str] = {}
    for serial, dev in serial_to_dev.items():
        id_to_serial[_norm(serial)] = serial
        if dev.get("mac"):
            id_to_serial[_norm(dev.get("mac"))] = serial

    status_by_switch: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if isinstance(switch_port_statuses_by_switch, dict):
        for serial, ports in switch_port_statuses_by_switch.items():
            if isinstance(ports, list):
                status_by_switch[serial] = {
                    str(p.get("portId")): p for p in ports if isinstance(p, dict)
                }

    parent_of: Dict[str, Tuple[str, str, str]] = {}
    children_of: Dict[str, List[str]] = {}
    edge_counts: Dict[str, int] = {}

    for local_serial, data in lldp_cdp.items() if isinstance(lldp_cdp, dict) else []:
        if local_serial not in serial_to_dev or not isinstance(data, dict):
            continue
        ports = data.get("ports", {})
        if not isinstance(ports, dict):
            continue
        for local_port, port_data in ports.items():
            if not isinstance(port_data, dict):
                continue
            port_status = status_by_switch.get(local_serial, {}).get(str(local_port), {})
            neighbor_serial = None
            neighbor_port = ""
            for key in ("lldp", "cdp"):
                disc = port_data.get(key)
                if not isinstance(disc, dict):
                    continue
                neighbor_port = str(disc.get("portId") or neighbor_port)
                for candidate in (
                    disc.get("chassisId"),
                    disc.get("deviceId"),
                    port_data.get("deviceMac"),
                ):
                    neighbor_serial = id_to_serial.get(_norm(candidate))
                    if neighbor_serial:
                        break
                if neighbor_serial:
                    break
            if not neighbor_serial or neighbor_serial == local_serial:
                continue

            local_type = serial_to_dev[local_serial].get("productType")
            neighbor_type = serial_to_dev[neighbor_serial].get("productType")
            if bool(port_status.get("isUplink")) and neighbor_type in ("switch", "appliance"):
                parent_of[local_serial] = (
                    neighbor_serial,
                    str(local_port),
                    neighbor_port,
                )
                children_of.setdefault(neighbor_serial, [])
                if local_serial not in children_of[neighbor_serial]:
                    children_of[neighbor_serial].append(local_serial)
            elif local_type == "switch" and neighbor_type in ("wireless", "camera", "sensor"):
                edge_counts[local_serial] = edge_counts.get(local_serial, 0) + 1

    return serial_to_dev, status_by_switch, parent_of, children_of, edge_counts

