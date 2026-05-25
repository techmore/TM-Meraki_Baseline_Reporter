"""Focused tests for topology SVG layout behavior."""
import re

from reporting.topology import _topo_pages, _topo_svg


def _switch(serial: str, name: str) -> dict:
    return {
        "serial": serial,
        "name": name,
        "model": "MS225-48FP",
        "productType": "switch",
        "status": "online",
    }


def _uplink(child: str, parent: str, port: str = "49") -> tuple:
    return (
        child,
        {
            "ports": {
                port: {
                    "lldp": {"chassisId": parent, "portId": "1"},
                    "cdp": {},
                }
            }
        },
    )


def _label_x(svg: str, label: str) -> float:
    match = re.search(
        rf'<text x="([0-9.]+)" y="[0-9.]+" text-anchor="middle"[^>]*>{re.escape(label)}</text>',
        svg,
    )
    assert match, f"Missing label {label!r}"
    return float(match.group(1))


def test_topology_orders_layers_by_parent_position_to_reduce_crossings():
    devices = [
        _switch("ROOT", "Root"),
        _switch("B", "Z-B"),
        _switch("C", "A-C"),
        _switch("D", "A-D"),
        _switch("E", "Z-E"),
    ]
    lldp = dict(
        [
            _uplink("B", "ROOT"),
            _uplink("C", "ROOT"),
            _uplink("D", "B"),
            _uplink("E", "C"),
        ]
    )
    ports = {
        serial: [{"portId": "49", "isUplink": True, "speed": "1 Gbps"}]
        for serial in ("B", "C", "D", "E")
    }

    svg = _topo_svg(devices, lldp, {}, {}, ports, show_internet=False)

    assert _label_x(svg, "A-C") < _label_x(svg, "Z-B")
    assert _label_x(svg, "Z-E") < _label_x(svg, "A-D")


def test_large_topology_overview_chunks_and_infers_distribution_parent_links():
    devices = [
        {
            "serial": "MX1",
            "name": "Firewall",
            "model": "MX95",
            "productType": "appliance",
            "status": "online",
        }
    ] + [_switch(f"SW{i:02d}", f"Dist-{i:02d}") for i in range(13)]

    pages = _topo_pages(devices, {}, {}, {}, {}, enrichment={})

    assert pages[0]["title"] == "Overview — Core / Distribution Layer (1/3)"
    assert pages[1]["title"] == "Overview — Core / Distribution Layer (2/3)"
    assert pages[2]["title"] == "Overview — Core / Distribution Layer (3/3)"
    assert 'stroke-dasharray="5 4"' in pages[0]["svg"]
    assert re.search(r'<svg[^>]*width="1332"', pages[0]["svg"])
