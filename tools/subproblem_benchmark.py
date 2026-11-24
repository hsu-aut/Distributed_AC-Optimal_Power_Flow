#!/usr/bin/env python3
"""Solve time of single local AC subproblems as a function of their size (no ADMM, no MPI).

    python tools/subproblem_benchmark.py --agents 1 2 3 4 8 16 30 59 118 --repeats 5 --out results/subproblem_benchmark

For each agent count k the partitions of `connected_impedance` are built (k = 118: one bus
each, sampled). Every partition is solved as the distributed agent would solve it: same
full AC model, strict local NLP settings, centered penalty, rho, 1 Gurobi thread. The
consensus targets z come from a reference operating point with a small seeded perturbation
per repeat, so repeats are warm-started solves with changing targets, as in ADMM.
The first solve of each partition includes the cold start and is reported separately.

Combine the per-size median warm solve time with the measured communication time per
iteration to judge from which subproblem size local computation dominates communication.
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SETTINGS = {
    "ADMM_STRICT": "1", "ADMM_JACOBI": "0", "ADMM_USE_SLACK": "0", "AC_BALANCE_SLACK_ALL": "0",
    "ADMM_ALLOW_SLACK_FALLBACK": "0", "AC_SOFT_THERMAL": "0", "AC_SOFT_GENERATION": "0", "AC_EXACT_THERMAL": "1",
    "AC_NATIVE_TRIG": "1", "AC_EXACT_BILINEAR": "1", "AC_DIRECT_NL": "1", "ADMM_CENTERED_PENALTY": "1",
    "ADMM_SOLVE_STAGGER_MS": "0", "GRB_THREADS": "1", "GRB_TIMELIMIT": "0", "GRB_NL_ITER_LIMIT": "1000",
    "GRB_FEAS_TOL": "1e-8", "GRB_MIP_GAP": "0", "GRB_MIP_GAP_ABS": "1e-8", "GRB_VERBOSE": "0",
    "GRB_OPTIMALITY_TARGET": "local", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
}


def reference_states(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if "states" in data:  # PYPOWER reference format
        return {int(b): s for b, s in data["states"].items()}
    central = data.get("central", data)
    return {int(b): {"v": central["v"][b], "va": central["theta"][b]} for b in central["v"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", nargs="+", type=int, default=[1, 2, 3, 4, 8, 16, 30, 59, 118])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--max-partitions", type=int, default=16, help="Sample at most this many partitions per k")
    ap.add_argument("--rho", type=float, default=1e6)
    ap.add_argument("--perturbation", type=float, default=1e-3, help="Relative perturbation of z per repeat")
    ap.add_argument("--reference", type=Path, default=ROOT / "validation/pypower_reference_20260915/reference_r1.json")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    os.environ.update(SETTINGS)
    os.environ["ADMM_RHO"] = os.environ["ADMM_RHO_V"] = os.environ["ADMM_RHO_THETA"] = str(args.rho)
    from distributed_opf.admm_ac import AcOpfSubnetProblem
    from distributed_opf.admm_params import AdmmParams
    from distributed_opf.config import load_topology_config
    from distributed_opf.partitioning import partition_connected_impedance
    from distributed_opf.pwl_ac import AcPwlParams

    topology = load_topology_config(ROOT / "topology_ieee118_full.yaml")
    reference = reference_states(args.reference)
    buses = sorted(topology.buses)
    rows = []
    for k in args.agents:
        assignment = partition_connected_impedance(buses, topology, k)
        owner = {b: r for r, bs in assignment.items() for b in bs}
        ranks = sorted(assignment)
        if len(ranks) > args.max_partitions:
            ranks = [ranks[int(i)] for i in np.linspace(0, len(ranks) - 1, args.max_partitions)]
        for rank in ranks:
            bus_ids = assignment[rank]
            cut_edges = {}
            for i in bus_ids:
                for j in topology.buses[i].neighbors:
                    if owner[j] != rank:
                        cut_edges.setdefault(owner[j], []).append((i, j))
            edges = sorted({e for es in cut_edges.values() for e in es})
            params = AdmmParams.from_env()
            build_start = time.perf_counter()
            problem = AcOpfSubnetProblem(agent_id=rank, bus_ids=bus_ids, topology=topology, params=params,
                                         cut_edges=cut_edges, pwl=AcPwlParams.from_env())
            build_s = time.perf_counter() - build_start
            times, statuses = [], []
            for repeat in range(args.repeats + 1):
                rng = np.random.default_rng(1000 * rank + repeat)
                noise = lambda: 1.0 + args.perturbation * rng.standard_normal()
                z_v = {(i, j): reference[j]["v"] * noise() for i, j in edges}
                z_th = {(i, j): reference[j]["va"] * noise() for i, j in edges}
                z_v_own = {(i, j): reference[i]["v"] * noise() for i, j in edges}
                z_th_own = {(i, j): reference[i]["va"] * noise() for i, j in edges}
                zero = {e: 0.0 for e in edges}
                neighbor_v, neighbor_theta = {}, {}
                for i, j in edges:
                    neighbor_v.setdefault(owner[j], {})[j] = z_v[(i, j)]
                    neighbor_theta.setdefault(owner[j], {})[j] = z_th[(i, j)]
                start = time.perf_counter()
                try:
                    problem.solve(neighbor_theta=neighbor_theta, neighbor_v=neighbor_v, z_v=z_v, u_v=zero, z_th=z_th,
                                  u_th=zero, z_v_own=z_v_own, u_v_own=zero, z_th_own=z_th_own, u_th_own=zero)
                    statuses.append(int(problem._model.Status))
                except RuntimeError as exc:
                    statuses.append(f"failed: {exc}")
                times.append(time.perf_counter() - start)
            warm = times[1:]
            rows.append({"agents": k, "rank": rank, "buses": len(bus_ids), "cut_edges": len(edges),
                         "build_s": build_s, "cold_solve_s": times[0], "warm_solve_s": warm,
                         "warm_median_s": statistics.median(warm) if warm else None, "statuses": statuses})
            print(f"k={k:3d} rank={rank:3d} buses={len(bus_ids):3d} cuts={len(edges):3d} "
                  f"build={build_s:.3f}s cold={times[0]*1000:.1f}ms warm median={statistics.median(warm)*1000:.1f}ms "
                  f"statuses={sorted(set(map(str, statuses)))}", flush=True)
    by_size = {}
    for row in rows:
        by_size.setdefault(row["buses"], []).append(row["warm_median_s"])
    summary = {str(size): {"partitions": len(v), "warm_median_ms": 1000 * statistics.median(v)} for size, v in sorted(by_size.items())}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "subproblem_benchmark.json").write_text(json.dumps({
        "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
        "rho": args.rho, "repeats": args.repeats, "perturbation": args.perturbation, "reference": str(args.reference),
        "settings": SETTINGS, "rows": rows, "by_size": summary}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
