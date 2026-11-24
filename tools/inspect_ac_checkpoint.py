"""Diagnose a complete checkpoint without changing or optimizing its state."""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config
from distributed_opf.ac_validation import validate_ac, AcTolerances
from distributed_opf.edge_penalty import EdgePenalty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--topology", type=Path, required=True)
    args = parser.parse_args()
    topo = load_topology_config(args.topology)
    states, rows, iterations = {}, [], set()
    edge_cache = {}

    def edge_factor(config, i, j):
        # Edge-weighted variant: rho_e = rho * f_e, exactly as in the solver's dual tolerance.
        if not config:
            return 1.0
        key = json.dumps(config, sort_keys=True)
        if key not in edge_cache:
            max_factor = config.get("max_factor")
            edge_cache[key] = EdgePenalty(topo, exponent=config["exponent"], min_factor=config["min_factor"],
                                          max_factor=math.inf if max_factor is None else max_factor)
        return edge_cache[key](i, j)
    for fp in sorted(args.folder.glob("rank_*.json")):
        data = json.loads(fp.read_text())
        iterations.add(data["iteration"])
        params = data["params"]
        rv = params["rho_v"] if params["rho_v"] is not None else params["rho"]
        rt = params["rho_th"] if params["rho_th"] is not None else params["rho"]
        bus, st = next(iter(data["states"].items()))
        ep = data.get("edge_penalty")
        if data["single_bus"]:
            b = int(bus)
            uv = [edge_factor(ep, b, int(j)) * v for j, v in list(st["uv"].items()) + list(st["uv_own"].items())]
            ut = [edge_factor(ep, b, int(j)) * v for j, v in list(st["uth"].items()) + list(st["uth_own"].items())]
            m = max(1, 4*len(st["uv"]))
        else:
            c = data["consensus"]
            uv = [edge_factor(ep, int(r[0]), int(r[1])) * r[2] for r in c["u_v"]+c["u_v_own"]]
            ut = [edge_factor(ep, int(r[0]), int(r[1])) * r[2] for r in c["u_th"]+c["u_th_own"]]
            m = max(1, 4*len(c["u_v"]))
        # Same convention as the solver's per-rank strict gate (agent_ac_mpi._check_convergence_local).
        eps_dual = math.sqrt(m)*params["abs_tol"]+params["rel_tol"]*math.sqrt(
            rv*rv*sum(v*v for v in uv)+rt*rt*sum(v*v for v in ut))
        rows.append({"rank": data["rank"], "primal_ratio": st["prim_res"]/params["abs_tol"],
            "dual_ratio": st["dual_res"]/eps_dual, "dual_residual": st["dual_res"], "dual_tolerance": eps_dual})
        for bus, value in data["states"].items():
            if int(bus) in states:
                raise ValueError("Duplicate bus in checkpoint")
            states[int(bus)] = value
    if len(iterations) != 1:
        raise ValueError("Checkpoint missing or mixed iterations")
    validation = validate_ac(topo, states, AcTolerances(balance=params["phys_tol"]))
    print(json.dumps({"iteration": next(iter(iterations)), "ranks": len(rows),
        "primal_failing_ranks": sum(r["primal_ratio"] > 1 for r in rows),
        "dual_failing_ranks": sum(r["dual_ratio"] > 1 for r in rows),
        "worst_dual": sorted(rows, key=lambda r: r["dual_ratio"], reverse=True)[:5],
        "validation": {k: v for k, v in validation.items() if k not in ("bus_details", "branches")}}, indent=2))


if __name__ == "__main__":
    main()
