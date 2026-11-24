#!/usr/bin/env python3
"""Compare the solver's stopping convention with the Boyd matrix-form criterion from residual traces.

    python tools/criterion_comparison.py results/criterion_check_20260915/p8 ... --levels 1e-4 3e-5 1e-5 --out results/criterion_check

Every trace row holds, per iteration, the quotients residual/tolerance at the run tolerance T_run
(eps_abs = eps_rel = T_run) for
  - the solver's per-rank strict gate (max over ranks; decision of the run),
  - the Boyd matrix form per rank (max over ranks) and globally (docs: consensus_residuals.py).
Scaling eps_abs and eps_rel together by c scales every quotient by 1/c exactly, so the first
iteration at which a criterion holds for a level T is the first row with
quotient * T_run / T <= 1 for both primal and dual quotients. Levels must be >= T_run. Only rows
present in the trace count; if a criterion is not met before the run ends, the result is
"not reached within <iterations>". The AC feasibility of the state at these iterations is not
part of this comparison (acceptance always requires the independent AC check).
"""
import argparse
import json
from pathlib import Path

import numpy as np

CRITERIA = {
    "solver_per_rank": ("max_rank_primal_ratio", "max_rank_dual_ratio"),
    "boyd_matrix_per_rank": ("boyd_a_max_rank_primal_ratio", "boyd_a_max_rank_dual_ratio"),
    "boyd_matrix_global": ("boyd_a_global_primal_ratio", "boyd_a_global_dual_ratio"),
}


def first_iteration(trace, col, primal, dual, scale):
    ok = (trace[:, col[primal]] * scale <= 1.0) & (trace[:, col[dual]] * scale <= 1.0)
    idx = np.flatnonzero(ok)
    return int(trace[idx[0], col["iteration"]]) if idx.size else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--levels", nargs="+", type=float, default=[1e-4, 3e-5, 1e-5])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = []
    for run in args.runs:
        manifest = json.loads((run / "manifest.json").read_text())
        run_tol = float(manifest["args"]["abs_tol"])
        if float(manifest["args"]["rel_tol"]) != run_tol:
            raise SystemExit(f"{run}: eps_abs and eps_rel must be equal for exact scaling")
        columns = json.loads((run / "logs" / "residual_trace_columns.json").read_text())
        if "boyd_a_global_dual_ratio" not in columns:
            raise SystemExit(f"{run}: trace has no Boyd matrix-form columns (run predates the reporting)")
        trace = np.load(run / "logs" / "residual_trace.npy")
        col = {c: i for i, c in enumerate(columns)}
        last = int(trace[-1, col["iteration"]])
        for level in args.levels:
            if level < run_tol:
                raise SystemExit(f"{run}: level {level:g} below run tolerance {run_tol:g}")
            entry = {"run": run.name, "agents": manifest["args"]["agents"], "rho": manifest["args"]["rho"],
                     "edge_rho": manifest["args"].get("edge_rho"), "run_tolerance": run_tol, "level": level,
                     "trace_iterations": last}
            for name, (primal, dual) in CRITERIA.items():
                entry[name] = first_iteration(trace, col, primal, dual, run_tol / level)
            s, b = entry["solver_per_rank"], entry["boyd_matrix_global"]
            entry["boyd_global_minus_solver"] = None if s is None or b is None else b - s
            rows.append(entry)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "criterion_comparison.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fmt = lambda v, last: f"{v}" if v is not None else f"> {last}"
    lines = ["| Lauf | Agenten | Stufe | Löserkriterium je Rang | Boyd-Matrixform je Rang | Boyd-Matrixform global |",
             "|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['run']} | {r['agents']} | {r['level']:g} | {fmt(r['solver_per_rank'], r['trace_iterations'])} | "
                     f"{fmt(r['boyd_matrix_per_rank'], r['trace_iterations'])} | {fmt(r['boyd_matrix_global'], r['trace_iterations'])} |")
    (args.out / "criterion_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
