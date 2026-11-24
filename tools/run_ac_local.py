#!/usr/bin/env python3
"""Isolated local raw-ADMM experiment. Never runs a power-flow polish.

Snapshots code and inputs before MPI starts. A zero exit requires both ADMM
convergence and independent full AC validation. Budget exhaustion is exit 2.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import platform
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config
from distributed_opf.partitioning import partition_graph_aware, compute_neighbor_ranks
from distributed_opf.partitioning import partition_connected_impedance, partition_statistics
from distributed_opf.initialization import build_initial_state, flat_reference, load_central_reference, write_initial_state
import yaml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="topology_ieee118_full.yaml")
    ap.add_argument("--agents", type=int, default=118)
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--rho", type=float, default=1000.)
    ap.add_argument("--rho-v", type=float, help="Fixed uniform penalty for voltage-magnitude consensus (default: --rho)")
    ap.add_argument("--rho-theta", type=float, help="Fixed uniform penalty for angle consensus (default: --rho)")
    ap.add_argument("--solve-limit", type=float, default=5., help="Seconds per local solve; 0 leaves the solver time limit unlimited")
    ap.add_argument("--nl-iterations", type=int, default=1000, help="Internal nonlinear barrier iteration budget per local solve")
    ap.add_argument("--centered-penalty", action="store_true", help="Equivalent ADMM penalty in deviation variables, with aggregation disabled")
    ap.add_argument("--timeout", type=float, default=1800.)
    ap.add_argument("--abs-tol", type=float, default=1e-5)
    ap.add_argument("--rel-tol", type=float, default=1e-5, help="Relative ADMM tolerance; scale together with --abs-tol")
    ap.add_argument("--phys-tol", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--threads", type=int, default=1, help="Gurobi threads per process (central and each agent); 0 = Gurobi default (all cores)")
    ap.add_argument("--partitioner", choices=("graph_aware", "connected_impedance"), default="graph_aware",
                    help="Bus-to-agent assignment for 1 < agents < buses; graph_aware reproduces all earlier runs")
    ap.add_argument("--edge-rho", choices=("none", "admittance"), default="none",
                    help="Separate ADMM variant: per-edge penalty rho*(|y_e|/median|y|)^exponent; none = standard ADMM")
    ap.add_argument("--edge-rho-exponent", type=float, default=1.0)
    ap.add_argument("--edge-rho-min-factor", type=float, default=0.0)
    ap.add_argument("--edge-rho-max-factor", type=float, default=0.0, help="0 = no upper clamp")
    ap.add_argument("--init", choices=("flat", "reference"), default="flat",
                    help="ADMM start: flat (V=1, theta=0) or V/theta/Pg/Qg of --init-reference")
    ap.add_argument("--init-reference", help="Central run directory, result.json, PYPOWER reference.json, or 'flat'")
    ap.add_argument("--init-perturbation", type=float, default=0.0, help="Relative Gaussian perturbation of the reference start (seeded)")
    ap.add_argument("--init-seed", type=int, default=0)
    ap.add_argument("--relaxation", type=float, default=1.0, help="ADMM over-relaxation alpha in (0, 2); 1 = standard ADMM (separate variant otherwise)")
    ap.add_argument("--adaptive-rho", action="store_true", help="Separate variant: residual balancing on the strict primal/dual quotients")
    ap.add_argument("--adaptive-mu", type=float, default=10.)
    ap.add_argument("--adaptive-tau", type=float, default=2.)
    ap.add_argument("--adaptive-period", type=int, default=50)
    ap.add_argument("--adaptive-until", type=int, default=0, help="Last iteration allowed to change rho; 0 = whole run")
    ap.add_argument("--rho-min", type=float, default=1e6)
    ap.add_argument("--rho-max", type=float, default=1e8)
    ap.add_argument("--log-every", type=int, default=1, help="Agent JSONL record every N iterations; 0 = final state only")
    ap.add_argument("--monitor-every", type=int, default=20, help="Global AC validation every N iterations (observation only); 0 = only at the convergence gate and the end")
    ap.add_argument("--checkpoint-every", type=int, default=500, help="Restart checkpoint every N iterations; 0 = final checkpoint only")
    ap.add_argument("--timing-trace", action="store_true", help="Store per-iteration phase times per rank (logs/timing_rank_*.npy)")
    ap.add_argument("--hostfile", help="Open MPI hostfile with worker nodes only (orchestrator excluded); requires a shared run directory, no --scratch")
    ap.add_argument("--central-host", help="Run the central reference on this worker node via mpirun -np 1 (fair hardware comparison)")
    ap.add_argument("--mpi-yield", action="store_true", help="Open MPI yields while waiting (oversubscribed local runs); does not change iterates")
    ap.add_argument("--optimality-target", choices=("local", "global"), default="local")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", action="store_true", help="Run on Linux temporary filesystem and copy artifacts to --out afterwards")
    ap.add_argument("--resume", help="Checkpoint iteration directory; --iterations is the total iteration budget")
    ap.add_argument("--central", action="store_true", help="Run the monolithic central reference with the same AC settings")
    args = ap.parse_args()
    if args.init == "reference" and not args.init_reference:
        raise ValueError("--init reference requires --init-reference")
    if args.init != "flat" and args.resume:
        raise ValueError("--init applies to fresh runs only")
    if args.init == "flat" and (args.init_reference or args.init_perturbation):
        raise ValueError("--init-reference/--init-perturbation require --init reference")
    if args.adaptive_rho and args.resume:
        raise ValueError("Resuming an adaptive-rho run is not supported: checkpoints store the current rho, not its history")
    if not 0.0 < args.relaxation < 2.0:
        raise ValueError("relaxation must lie in (0, 2)")
    if (args.hostfile or args.central_host) and args.scratch:
        raise ValueError("--hostfile/--central-host need a run directory visible on all nodes; do not use --scratch")
    if args.hostfile and args.central:
        raise ValueError("Use --central-host for the central reference")
    if args.central_host and not args.central:
        raise ValueError("--central-host only applies to --central")
    if args.central:
        if args.resume:
            raise ValueError("Central reference does not use ADMM checkpoints")
        args.agents = 1
    destination = Path(args.out).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    out = Path(tempfile.mkdtemp(prefix="ac_opf_")) if args.scratch else destination
    topo_path = (ROOT / args.topology).resolve()
    topo = load_topology_config(topo_path)
    if not 1 <= args.agents <= len(topo.buses):
        raise ValueError("agents must be between 1 and number of buses")
    shutil.copy2(topo_path, out / "topology.yaml")
    source = out / "source"
    (source / "distributed_opf").mkdir(parents=True)
    for p in (ROOT / "distributed_opf").glob("*.py"):
        shutil.copy2(p, source / "distributed_opf" / p.name)
    shutil.copy2(ROOT / "run_mpi_cluster.py", source / "run_mpi_cluster.py")
    shutil.copy2(ROOT / "central_ac_opf.py", source / "central_ac_opf.py")
    shutil.copy2(Path(__file__), source / "run_ac_local.py")
    env = os.environ.copy()
    # Remove inherited experiment switches, but retain license configuration.
    for key in list(env):
        if key.startswith(("ADMM_", "AC_", "CENTRAL_", "FAIR_")):
            del env[key]
    settings = {
        "ADMM_STRICT": "1", "ADMM_JACOBI": "0", "ADMM_RELAXATION": str(args.relaxation),
        "ADMM_ADAPTIVE_RHO": "1" if args.adaptive_rho else "0", "ADMM_ADAPTIVE_RHO_METRIC": "normalized",
        "ADMM_ADAPTIVE_RHO_MU": str(args.adaptive_mu), "ADMM_ADAPTIVE_RHO_TAU": str(args.adaptive_tau),
        "ADMM_ADAPTIVE_RHO_PERIOD": str(args.adaptive_period), "ADMM_ADAPTIVE_RHO_UNTIL": str(args.adaptive_until),
        "ADMM_RHO_MIN": str(args.rho_min), "ADMM_RHO_MAX": str(args.rho_max),
        "ADMM_EDGE_RHO": args.edge_rho, "ADMM_EDGE_RHO_EXPONENT": str(args.edge_rho_exponent),
        "ADMM_EDGE_RHO_MIN_FACTOR": str(args.edge_rho_min_factor), "ADMM_EDGE_RHO_MAX_FACTOR": str(args.edge_rho_max_factor),
        "ADMM_USE_SLACK": "0", "AC_BALANCE_SLACK_ALL": "0", "ADMM_ALLOW_SLACK_FALLBACK": "0",
        "AC_SOFT_THERMAL": "0", "AC_SOFT_GENERATION": "0", "AC_EXACT_THERMAL": "1",
        "AC_NATIVE_TRIG": "1", "AC_EXACT_BILINEAR": "1", "ADMM_RHO": str(args.rho),
        "AC_DIRECT_NL": "1",
        "ADMM_RHO_V": str(args.rho if args.rho_v is None else args.rho_v),
        "ADMM_RHO_THETA": str(args.rho if args.rho_theta is None else args.rho_theta),
        "ADMM_MAX_ITERS": str(args.iterations), "ADMM_ABS_TOL": str(args.abs_tol),
        "ADMM_REL_TOL": str(args.rel_tol), "ADMM_SLACK_TOL": "1e-6", "ADMM_PHYS_GATE": "1",
        "ADMM_PHYS_TOL": str(args.phys_tol), "ADMM_PHYS_CHECK_EVERY": str(args.monitor_every),
        "ADMM_LOG_EVERY": str(args.log_every), "ADMM_CHECKPOINT_EVERY": str(args.checkpoint_every),
        "ADMM_TIMING_TRACE": "1" if args.timing_trace else "0",
        "ADMM_BUILD_BATCH": str(max(1,args.batch)), "ADMM_SOLVE_BATCH": str(args.batch),
        "ADMM_SOLVE_STAGGER_MS": "0", "ADMM_CLOSE_PUSH_FACTOR": "1", "ADMM_BEST_ITERATE": "0",
        "ADMM_MPI_DEBUG": "0", "GRB_THREADS": str(args.threads), "GRB_TIMELIMIT": str(args.solve_limit),
        "GRB_NL_ITER_LIMIT": str(args.nl_iterations),
        "ADMM_CENTERED_PENALTY": "1" if args.centered_penalty else "0",
        "GRB_FEAS_TOL": "1e-8", "GRB_MIP_GAP": "0", "GRB_MIP_GAP_ABS": "1e-8", "GRB_VERBOSE": "0",
        "GRB_OPTIMALITY_TARGET": args.optimality_target, "LIVE_VIZ": "0",
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(source), "OPF_MODE": "ac",
    }
    env.update(settings)
    init_meta = {"mode": "flat"}
    if args.init == "reference":
        if args.init_reference == "flat":
            reference, reference_file, reference_hash = flat_reference(topo), "flat", None
        else:
            reference_path = Path(args.init_reference).resolve()
            reference_file = reference_path / "result.json" if reference_path.is_dir() else reference_path
            reference = load_central_reference(reference_path)
            reference_hash = hashlib.sha256(reference_file.read_bytes()).hexdigest()
        initial = build_initial_state(topo, reference, args.init_perturbation, args.init_seed)
        state_hash = write_initial_state(out / "init_state.json", initial)
        # The central reference reads the same state file through its own variable.
        init_key = "CENTRAL_INIT_STATE" if args.central else "ADMM_INIT_STATE"
        settings[init_key] = str(out / "init_state.json")
        env[init_key] = settings[init_key]
        init_meta = {"mode": "reference", "reference": str(reference_file), "reference_sha256": reference_hash,
                     "perturbation": args.init_perturbation, "seed": args.init_seed, "state_sha256": state_hash}
    if args.resume:
        resume = Path(args.resume).resolve()
        if len(list(resume.glob("rank_*.json"))) != args.agents:
            raise ValueError("Resume directory must contain one checkpoint per rank")
        shutil.copytree(resume, out / "resume")
        settings["ADMM_RESUME_DIR"] = str(out / "resume")
        env["ADMM_RESUME_DIR"] = settings["ADMM_RESUME_DIR"]
    env.setdefault("GRB_LICENSE_FILE", str(Path.home()/"gurobi.lic"))
    mpi = shutil.which("mpirun") or str(Path(sys.executable).parent/"mpirun")
    partition_meta = None
    mca = ["--mca", "mpi_yield_when_idle", "1"] if args.mpi_yield else []
    # Remote ranks only see exported variables: forward every experiment setting explicitly.
    forward = []
    for key in sorted(set(settings) | {"GRB_LICENSE_FILE", "GUROBI_HOME", "LD_LIBRARY_PATH"}):
        if key in env:
            forward += ["-x", key]
    placement = ["--oversubscribe", "--bind-to", "none"]
    hostfile_meta = None
    if args.hostfile:
        hostfile = Path(args.hostfile).resolve()
        slots = {}
        for line in hostfile.read_text(encoding="utf-8").splitlines():
            fields = line.split("#", 1)[0].split()
            if fields:
                count = next((int(f.split("=", 1)[1]) for f in fields[1:] if f.startswith("slots=")), 1)
                slots[fields[0]] = slots.get(fields[0], 0) + count
        if sum(slots.values()) < args.agents:
            raise ValueError(f"Hostfile provides {sum(slots.values())} slots for {args.agents} agents")
        shutil.copy2(hostfile, out / "hostfile.txt")
        placement = ["--hostfile", str(out / "hostfile.txt"), "--map-by", "slot", "--bind-to", "none"]
        hostfile_meta = {"source": str(hostfile), "hosts": len(slots), "slots": sum(slots.values()),
                         "max_slots_per_host": max(slots.values())}
    cmd = [mpi, *placement, *mca, *forward, "-np", str(args.agents),
           sys.executable, str(source/"run_mpi_cluster.py"), "--topology", str(out/"topology.yaml"), "--mode", "ac"]
    if args.central:
        cmd = [sys.executable, str(source/"central_ac_opf.py"), "--topology", str(out/"topology.yaml"),
               "--output-dir", str(out/"logs"), "--quiet"]
        if args.central_host:
            cmd = [mpi, "-np", "1", "--host", args.central_host, "--bind-to", "none", *forward, *cmd]
    elif args.agents != len(topo.buses):
        if args.partitioner == "connected_impedance":
            assignment = partition_connected_impedance(topo.buses, topo, args.agents)
        else:
            assignment = partition_graph_aware(topo.buses, topo, args.agents)
        neighbors = compute_neighbor_ranks(assignment, topo)
        partition_meta = {"strategy": args.partitioner, "statistics": partition_statistics(assignment, topo)}
        partition = {"partitioning": partition_meta,
                     "agents": [{"rank": r, "bus_ids": bs, "neighbor_ranks": neighbors[r]}
                                for r, bs in assignment.items()]}
        (out/"partition.yaml").write_text(yaml.safe_dump(partition), encoding="utf-8")
        cmd += ["--partition-config", str(out/"partition.yaml")]
    def git(*a):
        # Read-only queries: GIT_OPTIONAL_LOCKS=0 prevents `git status` from refreshing the index,
        # so a timed-out call cannot leave a stale .git/index.lock (seen from WSL on a Windows checkout).
        try:
            return subprocess.check_output(["git", "-C", str(ROOT), *a], timeout=30,
                                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).decode().strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return "unavailable (see exact source snapshot and SHA256 manifest)"
    hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob("*.py")}
    git_status = git("status", "--porcelain")
    metadata = {"args": vars(args), "commit": git("rev-parse", "HEAD"),
                "dirty": None if git_status.startswith("unavailable") else bool(git_status),
                "platform": platform.platform(), "python": sys.version,
                "logical_cpu_count": os.cpu_count(),
                "dependencies": {name: version(name) for name in ("numpy", "mpi4py", "gurobipy", "PyYAML")},
                "settings": settings, "source_sha256": hashes,
                "topology_sha256": hashlib.sha256((out/"topology.yaml").read_bytes()).hexdigest(),
                "command": cmd, "postprocessing": "none", "partition": partition_meta,
                "hostfile": hostfile_meta, "central_host": args.central_host,
                "launcher_host": platform.node(), "initialization": init_meta}
    (out/"manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (out/"source.patch").write_text(git("diff"), encoding="utf-8")
    start = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    with (out/"mpi.log").open("w") as log:
        p = subprocess.Popen(cmd, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        (destination/"active_run.json").write_text(json.dumps({"status": "running", "working_directory": str(out), "launcher_pid": p.pid}), encoding="utf-8")
        print(f"Running in {out}; launcher PID {p.pid}", flush=True)
        try:
            rc = p.wait(timeout=args.timeout)
            reason = "completed" if rc == 0 else ("process_error" if args.central else "mpi_error")
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            rc, reason = 124, "timeout"
    elapsed = time.perf_counter()-start
    summary_path = out/"logs"/("central_ac_results.json" if args.central else "admm_run_summary.json")
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    converged = summary.get("timing", {}).get("gurobi_status") in (2, 18) if args.central else summary.get("converged")
    success = rc == 0 and converged and summary.get("raw_ac_feasible")
    result = {"success": bool(success), "process_exit_code": rc, "mpi_exit_code": None if args.central else rc, "reason": reason,
              "started_at_utc": started_at, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "wall_total_s": elapsed, "wall_total_scope": "process launch through process exit; excludes snapshot preparation",
              "mode": "central" if args.central else "admm",
              "central" if args.central else "admm": summary}
    (out/"result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.scratch:
        shutil.copytree(out, destination, dirs_exist_ok=True)
    (destination/"active_run.json").write_text(json.dumps({"status": "finished", "working_directory": str(out), "launcher_pid": p.pid}), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
