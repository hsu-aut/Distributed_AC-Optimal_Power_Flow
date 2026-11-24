#!/usr/bin/env python3
"""Erzeuge feste Bus-Koordinaten für die Live-Visualisierung.

Beispiele:
  python tools/generate_network_layout.py --topology topology_ieee118_full.yaml
  python tools/generate_network_layout.py --topology topology_3bus.yaml
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _layout_ieee118() -> dict:
    import pandapower.networks as pn

    net = pn.case118()
    buses = {str(int(i)): {"x": float(r.x), "y": float(r.y)} for i, r in net.bus_geodata.iterrows()}
    return {
        "topology": "topology_ieee118_full.yaml",
        "source": "pandapower.networks.case118().bus_geodata",
        "buses": buses,
    }


def _layout_3bus() -> dict:
    return {
        "topology": "topology_3bus.yaml",
        "source": "hand-layout",
        "buses": {
            "0": {"x": 0.0, "y": 1.0},
            "1": {"x": -1.0, "y": 0.0},
            "2": {"x": 1.0, "y": 0.0},
        },
    }


def _layout_from_graph(topology_path: pathlib.Path) -> dict:
    """Fallback: spring_layout über Leitungsgraph (networkx oder reine Nachbarn)."""
    import yaml

    raw = yaml.safe_load(topology_path.read_text(encoding="utf-8"))
    try:
        import networkx as nx

        g = nx.Graph()
        for b in raw["buses"]:
            g.add_node(int(b["bus_id"]))
        for ln in raw["lines"]:
            g.add_edge(int(ln["from"]), int(ln["to"]))
        pos = nx.spring_layout(g, seed=42, weight=None)
        buses = {str(n): {"x": float(xy[0]), "y": float(xy[1])} for n, xy in pos.items()}
        source = "networkx.spring_layout(seed=42)"
    except ImportError:
        # Sehr grober Kreis-Fallback ohne networkx
        import math

        ids = sorted(int(b["bus_id"]) for b in raw["buses"])
        n = max(1, len(ids))
        buses = {}
        for k, bid in enumerate(ids):
            ang = 2.0 * math.pi * k / n
            buses[str(bid)] = {"x": math.cos(ang), "y": math.sin(ang)}
        source = "circle-fallback"
    return {"topology": topology_path.name, "source": source, "buses": buses}


def _with_bbox(payload: dict) -> dict:
    xs = [c["x"] for c in payload["buses"].values()]
    ys = [c["y"] for c in payload["buses"].values()]
    payload["n_buses"] = len(payload["buses"])
    payload["bbox"] = {
        "xmin": min(xs) if xs else 0.0,
        "xmax": max(xs) if xs else 1.0,
        "ymin": min(ys) if ys else 0.0,
        "ymax": max(ys) if ys else 1.0,
    }
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate network layout JSON for live viz")
    ap.add_argument("--topology", required=True, help="Topology YAML filename or path")
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON (default: layouts/<stem>_coords.json)",
    )
    args = ap.parse_args()

    topo_path = pathlib.Path(args.topology)
    if not topo_path.is_file():
        cand = ROOT / topo_path.name
        if cand.is_file():
            topo_path = cand
        else:
            print(f"[ERROR] Topology not found: {args.topology}")
            return 1

    name = topo_path.name.lower()
    if "ieee118" in name:
        payload = _layout_ieee118()
        default_out = ROOT / "layouts" / "ieee118_coords.json"
    elif "3bus" in name:
        payload = _layout_3bus()
        default_out = ROOT / "layouts" / "3bus_coords.json"
    else:
        payload = _layout_from_graph(topo_path)
        stem = topo_path.stem.replace("topology_", "")
        default_out = ROOT / "layouts" / f"{stem}_coords.json"

    payload = _with_bbox(payload)
    out = pathlib.Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({payload['n_buses']} buses, source={payload['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
