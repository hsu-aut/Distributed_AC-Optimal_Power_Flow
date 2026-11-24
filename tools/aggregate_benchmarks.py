#!/usr/bin/env python3
"""Aggregate repeated central and distributed AC-OPF runs into one comparison.

Every run must be accepted (central: solver success and AC validation; ADMM:
strict convergence and independent AC validation). Repeats of the same
configuration must end in bit-identical physical states (determinism check).
Timing statistics are reported as median/min/max over repeats; nothing is
extrapolated or averaged across different configurations.
"""
import argparse
import glob
import hashlib
import json
import statistics
from pathlib import Path


# Settings that do not change the ADMM iterates or the central solution (logging, budget, batching,
# paths). Every other recorded setting is part of the configuration key, so runs that differ in
# any iterate-relevant setting are never pooled as repetitions.
NON_ITERATE_SETTINGS = {
    "ADMM_LOG_EVERY", "ADMM_CHECKPOINT_EVERY", "ADMM_TIMING_TRACE", "ADMM_BUILD_BATCH", "ADMM_SOLVE_BATCH",
    "ADMM_SOLVE_STAGGER_MS", "ADMM_PHYS_CHECK_EVERY", "ADMM_MPI_DEBUG", "ADMM_MAX_ITERS", "ADMM_INIT_STATE",
    "ADMM_RESUME_DIR", "CENTRAL_INIT_STATE", "GRB_VERBOSE", "LIVE_VIZ", "PYTHONPATH", "PYTHONUNBUFFERED",
}


def _canonical(value):
    try:
        return f"{float(value):.12g}"
    except (TypeError, ValueError):
        return str(value)


def iterate_config(manifest):
    """Configuration of a run and its short hash: every iterate-relevant setting plus the software
    state (source hashes of all core modules) and the environment (platform, logical CPUs).

    Runs from a different code state or a different machine therefore never form a group, so they
    are never averaged as repetitions of one another (docs/MEASUREMENT_PROTOCOL.md §3). Settings
    that change only the runtime (checkpoint interval, logging, batching) are excluded from the key
    and must be kept constant within a series."""
    settings = manifest.get("settings") or {}
    args = manifest.get("args") or {}
    config = {k: _canonical(v) for k, v in settings.items() if k not in NON_ITERATE_SETTINGS}
    source = manifest.get("source_sha256") or {}
    config.update(topology=manifest.get("topology_sha256"), agents=args.get("agents"), central=bool(args.get("central")),
                  partition=(manifest.get("partition") or {}).get("strategy"),
                  initialization=json.dumps(manifest.get("initialization") or {"mode": "flat"}, sort_keys=True),
                  source=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:12],
                  platform=manifest.get("platform"), logical_cpus=manifest.get("logical_cpu_count"))
    return config, hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]


def digest(states):
    rows = [(int(b), repr(float(s["v"])), repr(float(s["va"])), repr(float(s["pg"])), repr(float(s["qg"])))
            for b, s in states.items()]
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def load_run(folder: Path):
    manifest = json.loads((folder / "manifest.json").read_text())
    result = json.loads((folder / "result.json").read_text())
    args, settings = manifest["args"], manifest["settings"]
    row = {"run": folder.name, "success": bool(result["success"]), "job_wall_s": result["wall_total_s"],
           "threads": settings.get("GRB_THREADS"), "started_at_utc": result.get("started_at_utc")}
    if args.get("central"):
        c = result["central"]
        t = c["timing"]
        init = (manifest.get("initialization") or {})
        init_label = "flat" if init.get("mode", "flat") == "flat" else f"ref:{Path(str(init.get('reference'))).parent.name or init.get('reference')}/p{init.get('perturbation')}/s{init.get('seed')}"
        config, config_hash = iterate_config(manifest)
        row.update(mode="central", agents=1, config=config,
                   key=f"central threads={settings.get('GRB_THREADS')} topo={manifest['topology_sha256'][:8]} init={init_label} cfg={config_hash}",
                   cost=c["generation_cost"], iterations=None,
                   load_s=t["python_wall_time_load"], build_s=t["python_wall_time_build"],
                   optimize_s=t["python_wall_time_optimize"], gurobi_runtime_s=t["gurobi_runtime"],
                   solver_status=t["gurobi_status"],
                   state_digest=digest({b: {"v": c["v"][b], "va": c["theta"][b], "pg": c["pg"][b], "qg": c["qg"][b]} for b in c["v"]}))
        return row
    s = result["admm"]
    ranks = json.loads((folder / "logs/rank_metrics.json").read_text())["ranks"]
    final = sorted((folder / "logs/checkpoints").glob("iter_*"))[-1]
    states = {}
    for path in final.glob("rank_*.json"):
        for bus, st in json.loads(path.read_text())["states"].items():
            states[bus] = st
    validation = json.loads((folder / "logs/ac_validation.json").read_text())
    phases = {name: [r["phases"][name]["total_s"] for r in ranks] for name in ranks[0]["phases"]}

    def total(*names):
        return sum(sum(phases.get(n, [0.0])) for n in names)

    # Runs from before the timing split have one "convergence" phase that contains the global
    # reductions, the local residual computation and the console output; it then counts as communication.
    split = "convergence_mpi" in phases
    mpi_s = total("vt_exchange", "solve_status", "consensus_exchange", "convergence_mpi" if split else "convergence")
    local_admm_s = total("zu_update", "convergence") if split else total("zu_update")
    output_s = total("io", "monitor", "console")
    loop_rank_s = len(ranks) * s["wall_iterations_s"]
    mpi_calls = sum(v["calls"] for r in ranks for v in r["mpi"].values())
    mpi_bytes = sum(v["input_buffer_bytes"] for r in ranks for v in r["mpi"].values())
    iterations = s["wall_iterations"]
    partition = (manifest.get("partition") or {}).get("strategy", "buswise" if args["agents"] == 118 else "none")
    init = (manifest.get("initialization") or {})
    init_label = "flat" if init.get("mode", "flat") == "flat" else f"ref/p{init.get('perturbation')}/s{init.get('seed')}"
    edge = settings.get("ADMM_EDGE_RHO", "none")
    edge_label = "none" if edge == "none" else f"{edge}^{settings.get('ADMM_EDGE_RHO_EXPONENT')}"
    # The readable part names the main settings; cfg hashes every iterate-relevant setting
    # (separate rho_v/rho_theta, relaxation, adaptive rho, solver options, ...).
    config, config_hash = iterate_config(manifest)
    row.update(mode="admm", agents=args["agents"], partitioner=partition, rho=args["rho"], config=config,
               key=(f"admm agents={args['agents']} partitioner={partition} rho={args['rho']:g} edge={edge_label} "
                    f"tol={float(settings.get('ADMM_ABS_TOL', 1e-5)):g}/{float(settings.get('ADMM_REL_TOL', 1e-5)):g} init={init_label} "
                    f"topo={manifest['topology_sha256'][:8]} threads={settings.get('GRB_THREADS')} cfg={config_hash}"),
               converged=s["converged"], feasible=s["raw_ac_feasible"], iterations=iterations,
               cost=validation["generation_cost"], max_dP=validation["max_dP"], max_dQ=validation["max_dQ"],
               loop_s=s["wall_iterations_s"], cpu_rank_sum_s=s["cpu_rank_sum_s"],
               solver_optimize_sum_s=sum(r["solver"]["optimize_wall_s"] for r in ranks),
               x_solve_sum_s=sum(phases["x_solve"]), x_solve_max_rank_s=max(phases["x_solve"]),
               x_solve_min_rank_s=min(phases["x_solve"]),
               waiting_exchange_sum_s=sum(phases["consensus_exchange"]) + sum(phases["solve_status"]),
               # Decomposition of rank-summed loop time. communication = blocking MPI calls incl. waiting;
               # local_admm = z/u-updates and residual computation; output = logs, checkpoints, console and
               # AC observation; overhead = remainder.
               timing_model="split" if split else "legacy: convergence phase (reductions, residuals, console) counted as communication",
               optimization_sum_s=total("x_solve"),
               communication_sum_s=mpi_s,
               local_admm_sum_s=local_admm_s,
               output_sum_s=output_s,
               overhead_sum_s=loop_rank_s - total("x_solve") - mpi_s - local_admm_s - output_s,
               communication_share=mpi_s / max(1e-12, loop_rank_s),
               largest_partition_buses=max(len(r["bus_ids"]) for r in ranks),
               loop_per_iteration_ms=1000 * s["wall_iterations_s"] / max(1, iterations),
               mpi_calls=mpi_calls, mpi_buffer_bytes=mpi_bytes,
               solver_statuses={k: sum(r["solver"]["statuses"].get(k, 0) for r in ranks)
                                for k in {kk for r in ranks for kk in r["solver"]["statuses"]}},
               state_digest=digest(states))
    return row


def spread(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", type=Path, help="Folders containing run directories")
    ap.add_argument("--reference-cost", type=float, default=129660.69406511678)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    runs = []
    for root in args.roots:
        for manifest in sorted(glob.glob(str(root / "*" / "manifest.json"))):
            folder = Path(manifest).parent
            if (folder / "result.json").exists():
                runs.append(load_run(folder))
    groups = {}
    for row in runs:
        groups.setdefault(row["key"], []).append(row)
    summary = []
    timing_fields = ("job_wall_s", "load_s", "build_s", "optimize_s", "gurobi_runtime_s", "loop_s", "cpu_rank_sum_s",
                     "solver_optimize_sum_s", "x_solve_sum_s", "x_solve_max_rank_s", "x_solve_min_rank_s",
                     "waiting_exchange_sum_s", "optimization_sum_s", "communication_sum_s", "local_admm_sum_s",
                     "output_sum_s", "overhead_sum_s",
                     "communication_share", "loop_per_iteration_ms", "mpi_calls", "mpi_buffer_bytes")
    for key, rows in sorted(groups.items(), key=lambda kv: (kv[1][0]["agents"], kv[0])):
        accepted = all(r["success"] for r in rows)
        entry = {"key": key, "mode": rows[0]["mode"], "agents": rows[0]["agents"], "repeats": len(rows),
                 "all_accepted": accepted,
                 "identical_final_states": len({r["state_digest"] for r in rows}) == 1,
                 "iterations": sorted({r["iterations"] for r in rows}, key=lambda x: (x is None, x)),
                 "config": rows[0].get("config"),
                 "software_state": (rows[0].get("config") or {}).get("source"),
                 "environment": (rows[0].get("config") or {}).get("platform"),
                 "timing_models": sorted({r.get("timing_model", "central") for r in rows}),
                 "cost_difference_pct": spread([100 * (r["cost"] - args.reference_cost) / args.reference_cost for r in rows]),
                 "runs": [r["run"] for r in rows]}
        for field in timing_fields:
            value = spread([r.get(field) for r in rows])
            if value is not None:
                entry[field] = value
        summary.append(entry)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "runs.json").write_text(json.dumps(runs, indent=2))
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    lines = ["| Konfiguration | Wdh. | akzeptiert | Endzustände identisch | Iterationen | Jobzeit Median [min–max] s | Schleife/Optimierung Median s | CPU-Summe Median s | ms je Iteration |",
             "|---|---:|---|---|---|---:|---:|---:|---:|"]
    for e in summary:
        job = e["job_wall_s"]
        core = e.get("loop_s") or e.get("optimize_s")
        cpu = e.get("cpu_rank_sum_s")
        per = e.get("loop_per_iteration_ms")
        lines.append(f"| {e['key']} | {e['repeats']} | {'ja' if e['all_accepted'] else 'NEIN'} | "
                     f"{'ja' if e['identical_final_states'] else 'nein'} | {e['iterations']} | "
                     f"{job['median']:.3f} [{job['min']:.3f}–{job['max']:.3f}] | {core['median']:.3f} | "
                     f"{cpu['median']:.1f} | " + (f"{per['median']:.2f}" if per else "–") + " |"
                     if cpu else
                     f"| {e['key']} | {e['repeats']} | {'ja' if e['all_accepted'] else 'NEIN'} | "
                     f"{'ja' if e['identical_final_states'] else 'nein'} | – | "
                     f"{job['median']:.3f} [{job['min']:.3f}–{job['max']:.3f}] | {core['median']:.3f} | – | – |")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(e["all_accepted"] and e["identical_final_states"] for e in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
