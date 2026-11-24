#!/usr/bin/env python3
"""Read-only progress view of a running or finished raw-ADMM experiment.

Evaluates every complete checkpoint: strict primal/dual ratios per rank (as in
tools/plot_rho_sweep.py), primal/dual split into V and theta, independent AC
mismatch and cost difference to a reference cost. Never modifies the run.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.ac_validation import validate_ac
from distributed_opf.config import load_topology_config


def edge_from_settings(settings, topology):
    """Rebuild the run's edge-penalty variant from its manifest (None = standard ADMM)."""
    import math
    from distributed_opf.edge_penalty import EdgePenalty
    if settings.get("ADMM_EDGE_RHO", "none") == "none":
        return None
    max_factor = float(settings.get("ADMM_EDGE_RHO_MAX_FACTOR", "0") or 0)
    return EdgePenalty(topology, float(settings.get("ADMM_EDGE_RHO_EXPONENT", "1")),
                       float(settings.get("ADMM_EDGE_RHO_MIN_FACTOR", "0") or 0),
                       max_factor if max_factor > 0 else math.inf)


def evaluate(folder: Path, topology, reference_cost: float, agents: int, edge=None):
    factor = (lambda i, j: 1.0) if edge is None else edge
    ranks = [json.loads(p.read_text()) for p in sorted(folder.glob("rank_*.json"))]
    if len(ranks) != agents:
        return None
    states, primal, dual = {}, [], []
    split = {"r_v": 0.0, "r_th": 0.0, "s_v": 0.0, "s_th": 0.0}
    for rank in ranks:
        p = rank["params"]
        rho_v, rho_th = p["rho_v"] or p["rho"], p["rho_th"] or p["rho"]
        if rank["single_bus"]:
            bus = int(next(iter(rank["states"])))
            state = next(iter(rank["states"].values()))
            u = {k: [(bus, int(j), x) for j, x in state[k].items()] for k in ("uv", "uv_own", "uth", "uth_own")}
            for j in state["zv"]:
                f2 = factor(bus, int(j)) ** 2
                split["r_v"] += (state["v_copy"][j]-state["zv"][j])**2 + (state["v"]-state["zv_own"][j])**2
                split["r_th"] += (state["va_copy"][j]-state["zth"][j])**2 + (state["va"]-state["zth_own"][j])**2
                split["s_v"] += f2*rho_v**2*((state["zv"][j]-state["_zv_prev"].get(j, state["zv"][j]))**2
                                             + (state["zv_own"][j]-state["_zv_own_prev"].get(j, state["zv_own"][j]))**2)
                split["s_th"] += f2*rho_th**2*((state["zth"][j]-state["_zth_prev"].get(j, state["zth"][j]))**2
                                               + (state["zth_own"][j]-state["_zth_own_prev"].get(j, state["zth_own"][j]))**2)
        else:
            state = next(iter(rank["states"].values()))
            u = {k: [(int(e[0]), int(e[1]), e[2]) for e in rank["consensus"][s]] for k, s in
                 (("uv", "u_v"), ("uv_own", "u_v_own"), ("uth", "u_th"), ("uth_own", "u_th_own"))}
        dimension = max(1, 4*len(u["uv"]))
        norm = math.sqrt(sum((x*(rho_v if k.startswith("uv") else rho_th)*factor(i, j))**2
                             for k, xs in u.items() for i, j, x in xs))
        eps_dual = math.sqrt(dimension)*p["abs_tol"] + p["rel_tol"]*norm
        primal.append(state["prim_res"]/p["abs_tol"])
        dual.append(state["dual_res"]/eps_dual)
        for bus, value in rank["states"].items():
            states[int(bus)] = value
    validation = validate_ac(topology, states)
    return {"iteration": ranks[0]["iteration"], "max_primal_ratio": max(primal), "max_dual_ratio": max(dual),
            "median_dual_ratio": sorted(dual)[len(dual)//2], "ranks_dual_ok": sum(d <= 1 for d in dual),
            "primal_V": math.sqrt(split["r_v"]), "primal_theta": math.sqrt(split["r_th"]),
            "dual_V": math.sqrt(split["s_v"]), "dual_theta": math.sqrt(split["s_th"]),
            "max_dP": validation["max_dP"], "max_dQ": validation["max_dQ"], "feasible": validation["feasible"],
            "cost_difference_pct": 100*(validation["generation_cost"]-reference_cost)/abs(reference_cost)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path, help="Run folder (or scratch working directory) containing topology.yaml, manifest.json, logs/")
    ap.add_argument("--reference-cost", type=float, default=129660.69406511678)
    ap.add_argument("--every", type=int, default=500)
    args = ap.parse_args()
    manifest = json.loads((args.run/"manifest.json").read_text())
    topology = load_topology_config(args.run/"topology.yaml")
    rows = []
    for folder in sorted((args.run/"logs/checkpoints").glob("iter_*")):
        if int(folder.name[5:]) % args.every:
            continue
        row = evaluate(folder, topology, args.reference_cost, manifest["args"]["agents"],
                       edge_from_settings(manifest.get("settings", {}), topology))
        if row:
            rows.append(row)
            print(json.dumps({k: (float(f"{v:.4g}") if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
    return rows


if __name__ == "__main__":
    main()
