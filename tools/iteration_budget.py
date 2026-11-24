#!/usr/bin/env python3
"""Iteration budget for cluster runs from recorded checkpoint histories.

    python tools/iteration_budget.py results/phase5_partitions_20260914/ci_p8 ... --out results/budget

For each run (continuations can be listed as separate folders; they are merged per
configuration by iteration) the first checkpoint iteration is determined at which the
state is independently AC-feasible AND the generation cost lies within +-2 %, +-1 %,
+-0.1 % of the central reference, and the iteration of strict ADMM convergence.
Checkpoint spacing limits the resolution (milestones are reported as "<= iteration").

ADMM tolerance levels: with eps_abs and eps_rel both scaled by a factor c, every per-rank
quotient residual/tolerance scales exactly by 1/c. The first checkpoint at which all ranks
satisfy primal and dual quotient <= c is the convergence iteration for tolerance c*1e-5
(c = 10 -> 1e-4, c = 100 -> 1e-3). For each level the independent AC feasibility and the
cost deviation at that iteration are reported, so a looser tolerance is only acceptable
where the composed state is still feasible.

Recommended budgets:
- quality budget: first iteration with feasibility and +-1 % (rounded up to 500) x 1.25
- strict budget: strict convergence iteration x 1.25 (rounded up to 1000); iterations are
  deterministic for identical software, the margin covers platform floating-point effects.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config  # noqa: E402
from tools.monitor_checkpoints import edge_from_settings, evaluate  # noqa: E402

LEVELS = (2.0, 1.0, 0.1)
TOLERANCE_FACTORS = (100.0, 10.0, 3.0)


def configuration(manifest):
    a, s = manifest["args"], manifest["settings"]
    part = (manifest.get("partition") or {}).get("strategy", "buswise")
    return (f"agents={a['agents']} {part} rho={a['rho']:g} edge={s.get('ADMM_EDGE_RHO', 'none')}"
            f" exp={s.get('ADMM_EDGE_RHO_EXPONENT', '-')} init={(manifest.get('initialization') or {}).get('mode', 'flat')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--reference-cost", type=float, default=129660.69406511678)
    ap.add_argument("--safety", type=float, default=1.25)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    groups = {}
    for run in args.runs:
        manifest = json.loads((run / "manifest.json").read_text())
        topology = load_topology_config(run / "topology.yaml")
        edge = edge_from_settings(manifest.get("settings", {}), topology)
        points = []
        for folder in sorted((run / "logs" / "checkpoints").glob("iter_*")):
            row = evaluate(folder, topology, args.reference_cost, manifest["args"]["agents"], edge)
            if row:
                points.append(row)
        result = json.loads((run / "result.json").read_text()) if (run / "result.json").exists() else {}
        converged = (result.get("admm") or {}).get("converged")
        entry = groups.setdefault(configuration(manifest), {"runs": [], "points": {}, "strict_iteration": None})
        entry["runs"].append(str(run))
        for p in points:
            entry["points"][p["iteration"]] = p
        if converged:
            entry["strict_iteration"] = result["admm"]["wall_iterations"]
    report = []
    for key, entry in sorted(groups.items()):
        points = [entry["points"][i] for i in sorted(entry["points"])]
        milestones = {}
        for level in LEVELS:
            hit = next((p["iteration"] for p in points if p["feasible"] and abs(p["cost_difference_pct"]) <= level), None)
            milestones[f"feasible_within_{level:g}pct"] = hit
        tolerance_levels = {}
        for factor in TOLERANCE_FACTORS:
            hit = next((p for p in points if p["max_primal_ratio"] <= factor and p["max_dual_ratio"] <= factor), None)
            tolerance_levels[f"{factor * 1e-5:.0e}"] = None if hit is None else {
                "iteration": hit["iteration"], "feasible": hit["feasible"], "max_dP": hit["max_dP"],
                "cost_difference_pct": hit["cost_difference_pct"]}
        strict = entry["strict_iteration"]
        one = milestones["feasible_within_1pct"]
        report.append({"configuration": key, "runs": entry["runs"], "checkpoints": len(points),
                       "last_checkpoint": points[-1]["iteration"] if points else None,
                       **milestones, "tolerance_levels": tolerance_levels, "strict_convergence_iteration": strict,
                       "recommended_quality_budget": None if one is None else int(math.ceil(one * args.safety / 500) * 500),
                       "recommended_strict_budget": None if strict is None else int(math.ceil(strict * args.safety / 1000) * 1000)})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "iteration_budget.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for r in report:
        print(f"{r['configuration']}: <=2% {r['feasible_within_2pct']} <=1% {r['feasible_within_1pct']} "
              f"<=0.1% {r['feasible_within_0.1pct']} strikt {r['strict_convergence_iteration']} "
              f"-> Budget Qualität {r['recommended_quality_budget']} / strikt {r['recommended_strict_budget']} "
              f"(Checkpoints {r['checkpoints']}, bis {r['last_checkpoint']})")
        for tol, hit in r["tolerance_levels"].items():
            print(f"    ADMM-Toleranz {tol}: " + ("nicht erreicht" if hit is None else
                  f"Iteration <= {hit['iteration']}, AC-zulässig {hit['feasible']}, max dP {hit['max_dP']:.1e}, Kosten {hit['cost_difference_pct']:+.4f} %"))


if __name__ == "__main__":
    main()
