#!/usr/bin/env python3
"""Figures and tables for the thesis from accepted run directories.

    python tools/thesis_figures.py results/benchmark_local_20260914 --out results/figures

Produces (PNG + PDF, and CSV/JSON with the plotted numbers):
1. residual_curves      max-rank primal/dual quotients per iteration for each configuration
2. time_decomposition   rank-summed loop time split into local optimization, communication, overhead
3. subproblem_size      per rank: buses in the partition vs. local optimization time and waiting time
4. summary_table        iterations, cost deviation, balance errors, job and loop time

Only runs with result.json success=true are used (strict ADMM convergence and independent
AC validation, or accepted central solve). For repeated configurations the first accepted
run (lexicographic name) is plotted; tables list all runs.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate_benchmarks import iterate_config  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
REFERENCE_COST = 129660.69406511678  # original MATPOWER case118; PGLib runs report their own central cost
OPTIMIZATION = ("x_solve",)
OUTPUT = ("io", "monitor", "console")


def phase_groups(phases):
    """Phase names per category. Runs before the timing split count their whole convergence phase
    (reductions, residual computation, console output) as communication."""
    if "convergence_mpi" in phases:
        return {"communication": ("vt_exchange", "consensus_exchange", "solve_status", "convergence_mpi"),
                "local_admm": ("zu_update", "convergence"), "model": "split"}
    return {"communication": ("vt_exchange", "consensus_exchange", "solve_status", "convergence"),
            "local_admm": ("zu_update",), "model": "legacy"}


def accepted_runs(roots):
    runs = []
    for root in roots:
        for manifest in sorted(Path(root).glob("*/manifest.json")):
            folder = manifest.parent
            result_path = folder / "result.json"
            if not result_path.exists():
                continue
            result = json.loads(result_path.read_text())
            if not result.get("success"):
                continue
            runs.append((folder, json.loads(manifest.read_text()), result))
    return runs


PGLIB_TOPOLOGY_PREFIX = None  # set from topology_ieee118_pglib.yaml when present


def label(manifest):
    """Unique, readable configuration label: every iterate-changing setting is included."""
    args, settings = manifest["args"], manifest["settings"]
    variant = ""
    if PGLIB_TOPOLOGY_PREFIX and manifest.get("topology_sha256", "").startswith(PGLIB_TOPOLOGY_PREFIX):
        variant = " [PGLib]"
    init = manifest.get("initialization") or {}
    init_text = "" if init.get("mode", "flat") == "flat" else f", Start Ref. {init.get('perturbation', 0):g}/{init.get('seed', 0)}"
    if args.get("central"):
        return f"zentral ({settings.get('GRB_THREADS')} Thr.){init_text}{variant}"
    edge = settings.get("ADMM_EDGE_RHO", "none")
    edge_text = "" if edge == "none" else f", kantengew. p={float(settings.get('ADMM_EDGE_RHO_EXPONENT', 1)):g}"
    tol = float(settings.get("ADMM_ABS_TOL", 1e-5))
    tol_text = "" if tol == 1e-5 else f", tol {tol:g}"
    strategy = (manifest.get("partition") or {}).get("strategy", "busweise" if args["agents"] == 118 else "?")
    name = "118 Busagenten" if args["agents"] == 118 else f"{args['agents']} Partitionen ({strategy})"
    return f"{name}, ρ={args['rho']:g}{edge_text}{tol_text}{init_text}{variant}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    args.out.mkdir(parents=True, exist_ok=True)
    global PGLIB_TOPOLOGY_PREFIX
    pglib = ROOT_DIR / "topology_ieee118_pglib.yaml"
    if pglib.exists():
        import hashlib
        PGLIB_TOPOLOGY_PREFIX = hashlib.sha256(pglib.read_bytes()).hexdigest()
    runs = accepted_runs(args.roots)
    admm = [(f, m, r) for f, m, r in runs if not m["args"].get("central")]
    # One plotted run per configuration. Labels name the main settings; if two different configurations
    # (iterate_config hash) share a label, the hash is appended so they are never merged.
    hashes = {}
    for f, m, r in admm:
        hashes.setdefault(label(m), set()).add(iterate_config(m)[1])
    first = {}
    for f, m, r in sorted(admm, key=lambda item: item[0].name):
        name = label(m) if len(hashes[label(m)]) == 1 else f"{label(m)} [cfg {iterate_config(m)[1]}]"
        first.setdefault(name, (f, m, r))

    # 1) Residual curves
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    curves = {}
    for name, (folder, _, _) in sorted(first.items()):
        trace_file = folder / "logs" / "residual_trace.npy"
        if not trace_file.exists():
            continue
        columns = json.loads((folder / "logs" / "residual_trace_columns.json").read_text())
        trace = np.load(trace_file)
        col = {c: i for i, c in enumerate(columns)}
        axes[0].semilogy(trace[:, col["iteration"]], trace[:, col["max_rank_primal_ratio"]], label=name)
        axes[1].semilogy(trace[:, col["iteration"]], trace[:, col["max_rank_dual_ratio"]], label=name)
        curves[name] = {"iterations": int(trace[-1, col["iteration"]]),
                        "final_primal_ratio": float(trace[-1, col["max_rank_primal_ratio"]]),
                        "final_dual_ratio": float(trace[-1, col["max_rank_dual_ratio"]])}
    for ax, title in zip(axes, ("Primales Residuum / Toleranz (max. Rang)", "Duales Residuum / Toleranz (max. Rang)")):
        ax.axhline(1.0, color="black", linestyle="--", linewidth=0.8)
        ax.set_xlabel("ADMM-Iteration")
        ax.set_title(title)
        ax.grid(True, alpha=0.2)
    if curves:
        axes[0].legend(fontsize=8)
    fig.savefig(args.out / "residual_curves.png", dpi=160)
    fig.savefig(args.out / "residual_curves.pdf")
    plt.close(fig)
    (args.out / "residual_curves.json").write_text(json.dumps(curves, indent=2), encoding="utf-8")

    # 2) Time decomposition and 3) subproblem size
    decomposition, per_rank = [], []
    for name, (folder, manifest, result) in sorted(first.items(), key=lambda item: item[1][1]["args"]["agents"]):
        ranks = json.loads((folder / "logs" / "rank_metrics.json").read_text())["ranks"]
        loop = result["admm"]["wall_iterations_s"]
        total = loop * len(ranks)
        groups = phase_groups(ranks[0]["phases"])

        def phase_total(names, rank_list=ranks):
            return sum(r["phases"].get(p, {}).get("total_s", 0.0) for r in rank_list for p in names)

        optimization = phase_total(OPTIMIZATION)
        communication = phase_total(groups["communication"])
        local_admm = phase_total(groups["local_admm"])
        output = phase_total(OUTPUT)
        decomposition.append({"configuration": name, "agents": len(ranks), "iterations": result["admm"]["wall_iterations"],
                              "timing_model": groups["model"], "loop_s": loop,
                              "optimization_share": optimization / total, "communication_share": communication / total,
                              "local_admm_share": local_admm / total, "output_share": output / total,
                              "overhead_share": max(0.0, 1.0 - (optimization + communication + local_admm + output) / total),
                              "ms_per_iteration": 1000 * loop / result["admm"]["wall_iterations"]})
        for r in ranks:
            per_rank.append({"configuration": name, "rank": r["rank"], "buses": len(r["bus_ids"]),
                             "optimization_s_per_iteration": r["phases"]["x_solve"]["total_s"] / result["admm"]["wall_iterations"],
                             "waiting_s_per_iteration": phase_total(groups["communication"], [r]) / result["admm"]["wall_iterations"]})
    if decomposition:
        fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
        names = [d["configuration"] for d in decomposition]
        bottom = np.zeros(len(names))
        for key, text in (("optimization_share", "lokale Optimierung"), ("local_admm_share", "lokale ADMM-Rechnung"),
                          ("communication_share", "MPI inkl. Warten"), ("output_share", "Ausgabe/Beobachtung"),
                          ("overhead_share", "Overhead")):
            values = np.array([d[key] for d in decomposition])
            ax.bar(names, values, bottom=bottom, label=text)
            bottom += values
        ax.set_ylabel("Anteil an der Rang-Schleifenzeit")
        ax.legend()
        ax.tick_params(axis="x", rotation=20)
        fig.savefig(args.out / "time_decomposition.png", dpi=160)
        fig.savefig(args.out / "time_decomposition.pdf")
        plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
        for name in names:
            rows = [p for p in per_rank if p["configuration"] == name]
            axes[0].scatter([p["buses"] for p in rows], [1000 * p["optimization_s_per_iteration"] for p in rows], label=name, s=18)
            axes[1].scatter([p["buses"] for p in rows], [1000 * p["waiting_s_per_iteration"] for p in rows], label=name, s=18)
        axes[0].set_ylabel("lokale Optimierung je Iteration [ms]")
        axes[1].set_ylabel("MPI inkl. Warten je Iteration [ms]")
        for ax in axes:
            ax.set_xlabel("Busse im Teilproblem")
            ax.grid(True, alpha=0.2)
        axes[0].legend(fontsize=8)
        fig.savefig(args.out / "subproblem_size.png", dpi=160)
        fig.savefig(args.out / "subproblem_size.pdf")
        plt.close(fig)
    (args.out / "time_decomposition.json").write_text(json.dumps(decomposition, indent=2), encoding="utf-8")
    with open(args.out / "subproblem_size.csv", "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["configuration", "rank", "buses", "optimization_s_per_iteration",
                                                    "waiting_s_per_iteration"])
        writer.writeheader()
        writer.writerows(per_rank)

    # 4) Summary table of all accepted runs
    rows = []
    for folder, manifest, result in runs:
        if manifest["args"].get("central"):
            central = result["central"]
            rows.append({"run": folder.name, "configuration": label(manifest), "iterations": "",
                         "cost_deviation_pct": (100 * (central["generation_cost"] - REFERENCE_COST) / REFERENCE_COST
                                                if "[PGLib]" not in label(manifest) else None),
                         "max_dP": "", "max_dQ": "", "job_s": result["wall_total_s"],
                         "core_s": central["timing"]["gurobi_runtime"]})
            continue
        validation = json.loads((folder / "logs" / "ac_validation.json").read_text())
        rows.append({"run": folder.name, "configuration": label(manifest), "iterations": result["admm"]["wall_iterations"],
                     "cost_deviation_pct": (100 * (validation["generation_cost"] - REFERENCE_COST) / REFERENCE_COST
                                            if "[PGLib]" not in label(manifest) else None),
                     "max_dP": validation["max_dP"], "max_dQ": validation["max_dQ"],
                     "job_s": result["wall_total_s"], "core_s": result["admm"]["wall_iterations_s"]})
    with open(args.out / "summary_table.csv", "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["run"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(runs)} accepted runs, {len(first)} ADMM configurations plotted -> {args.out}")


if __name__ == "__main__":
    main()
