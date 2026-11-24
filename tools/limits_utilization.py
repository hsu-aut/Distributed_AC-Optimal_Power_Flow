#!/usr/bin/env python3
"""Utilization of operating limits for accepted central and distributed solutions.

    python tools/limits_utilization.py results/benchmark_local_20260914 --out results/limits

For every accepted run: voltage margins to the band, generators at P/Q limits, branch
apparent power and angle differences (the MATPOWER case118 has no thermal ratings,
RATE_A = 0), the independent AC mismatch, and the deviation of the operating point
from the first accepted central run (V, theta, Pg, Qg).
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.ac_validation import validate_ac  # noqa: E402
from distributed_opf.config import load_topology_config  # noqa: E402

BINDING = 1e-4  # p.u.; a quantity within this distance of its bound counts as "at the limit"


def final_states(folder: Path, manifest, result):
    if manifest["args"].get("central"):
        c = result["central"]
        return {int(b): {"v": c["v"][b], "va": c["theta"][b], "pg": c["pg"][b], "qg": c["qg"][b]} for b in c["v"]}
    last = sorted((folder / "logs" / "checkpoints").glob("iter_*"))[-1]
    states = {}
    for path in last.glob("rank_*.json"):
        for bus, st in json.loads(path.read_text())["states"].items():
            states[int(bus)] = {k: st[k] for k in ("v", "va", "pg", "qg")}
    return states


def utilization(topology, states):
    validation = validate_ac(topology, states)
    v_low = [(b, states[b]["v"] - bus.v_min_pu) for b, bus in topology.buses.items()]
    v_high = [(b, bus.v_max_pu - states[b]["v"]) for b, bus in topology.buses.items()]
    gens = [(b, bus) for b, bus in topology.buses.items() if bus.is_generator]
    at = lambda values: sorted(b for b, margin in values if margin <= BINDING)
    branches = validation["branches"]
    angle = [abs(states[l.from_bus]["va"] - states[l.to_bus]["va"]) for l in topology.lines]
    return {
        "feasible": validation["feasible"], "max_dP": validation["max_dP"], "max_dQ": validation["max_dQ"],
        "generation_cost": validation["generation_cost"],
        "voltage_min_margin_pu": min(m for _, m in v_low), "voltage_max_margin_pu": min(m for _, m in v_high),
        "buses_at_vmin": at(v_low), "buses_at_vmax": at(v_high),
        "generators_at_pmin": at([(b, states[b]["pg"] - bus.pg_min) for b, bus in gens]),
        "generators_at_pmax": at([(b, bus.pg_max - states[b]["pg"]) for b, bus in gens]),
        "generators_at_qmin": at([(b, states[b]["qg"] - bus.qg_min) for b, bus in gens]),
        "generators_at_qmax": at([(b, bus.qg_max - states[b]["qg"]) for b, bus in gens]),
        "branches_with_thermal_limit": sum(1 for br in branches if br["limit"] is not None) // 2,
        "max_branch_apparent_power_pu": max(br["s"] for br in branches),
        "max_branch_loading_pct": max((100 * br["s"] / br["limit"] for br in branches if br["limit"]), default=None),
        "branch_ends_at_thermal_limit": sorted({(br["branch_id"], br["from"]) for br in branches
                                                if br["limit"] and br["limit"] - br["s"] <= BINDING}),
        "max_thermal_violation": validation["max_thermal_violation"],
        "max_angle_violation": validation["max_angle_violation"],
        "max_angle_difference_deg": math.degrees(max(angle)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows, central_states, central_topology_hash = [], None, None
    for root in args.roots:
        for manifest_path in sorted(root.glob("*/manifest.json")):
            folder = manifest_path.parent
            if not (folder / "result.json").exists():
                continue
            manifest = json.loads(manifest_path.read_text())
            result = json.loads((folder / "result.json").read_text())
            if not result.get("success"):
                continue
            states = final_states(folder, manifest, result)
            # Each run is evaluated with its own topology snapshot (e.g. PGLib variant with ratings).
            topology = load_topology_config(folder / "topology.yaml")
            if manifest["args"].get("central") and central_states is None:
                central_states, central_topology_hash = states, manifest["topology_sha256"]
            rows.append((folder, manifest, states, topology))
    if central_states is None:
        raise SystemExit("Need at least one accepted central run for the comparison")
    report = []
    for folder, manifest, states, topology in rows:
        if manifest["topology_sha256"] != central_topology_hash:
            raise SystemExit(f"{folder}: topology differs from the central reference; evaluate variants separately")
        entry = {"run": str(folder), "central": bool(manifest["args"].get("central")),
                 "agents": manifest["args"]["agents"], "topology_sha256": manifest["topology_sha256"],
                 **utilization(topology, states)}
        entry["deviation_from_central"] = {
            "max_abs_dV_pu": max(abs(states[b]["v"] - central_states[b]["v"]) for b in states),
            "max_abs_dtheta_deg": math.degrees(max(abs(states[b]["va"] - central_states[b]["va"]) for b in states)),
            "max_abs_dPg_MW": 100 * max(abs(states[b]["pg"] - central_states[b]["pg"]) for b in states),
            "max_abs_dQg_MVar": 100 * max(abs(states[b]["qg"] - central_states[b]["qg"]) for b in states)}
        central_util = utilization(topology, central_states)
        same = lambda key: entry[key] == central_util[key]
        entry["same_binding_sets_as_central"] = all(same(k) for k in (
            "buses_at_vmin", "buses_at_vmax", "generators_at_pmin", "generators_at_pmax", "generators_at_qmin", "generators_at_qmax",
        "branch_ends_at_thermal_limit"))
        report.append(entry)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "limits_utilization.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    seen = set()
    for e in report:
        key = (e["central"], e["agents"])
        if key in seen:
            continue
        seen.add(key)
        d = e["deviation_from_central"]
        print(f"{'zentral' if e['central'] else str(e['agents']) + ' Agenten':>12}: feasible={e['feasible']} "
              f"V-Rand min {e['voltage_min_margin_pu']:.2e}/{e['voltage_max_margin_pu']:.2e} "
              f"an Vmax {len(e['buses_at_vmax'])} an Vmin {len(e['buses_at_vmin'])} "
              f"Pmax {len(e['generators_at_pmax'])} Pmin {len(e['generators_at_pmin'])} Qmax {len(e['generators_at_qmax'])} Qmin {len(e['generators_at_qmin'])} "
              f"|S|max {e['max_branch_apparent_power_pu']:.2f} p.u. Auslastung max {e['max_branch_loading_pct']} % an Grenze {len(e['branch_ends_at_thermal_limit'])} Δθmax {e['max_angle_difference_deg']:.1f}° "
              f"gleiche aktive Grenzen {e['same_binding_sets_as_central']} "
              f"ΔV {d['max_abs_dV_pu']:.1e} ΔPg {d['max_abs_dPg_MW']:.2e} MW")


if __name__ == "__main__":
    main()
