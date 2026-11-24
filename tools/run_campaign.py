#!/usr/bin/env python3
"""Run a case-study campaign from cluster/case_studies.yaml.

Cluster (on the orchestrator pi119, shared run directory):
    python tools/run_campaign.py --out results/cluster_YYYYMMDD --hostfiles cluster/hostfiles --central-host pi1
Local (same runs, no hostfile, Linux scratch directory):
    python tools/run_campaign.py --out results/local_campaign --local --select C1_partitions
Preview only:
    python tools/run_campaign.py --out results/x --dry-run

Repetition r runs every selected configuration once; the order of configurations is
rotated per repetition. A run whose result.json already exists is skipped, so an
interrupted campaign continues where it stopped. Every run is a fresh, uninterrupted
tools/run_ac_local.py call; results are evaluated with tools/aggregate_benchmarks.py.
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def planned_runs(config, selected, repeats_override=None):
    tolerances = config["tolerances"]
    configurations = []
    for case in config["cases"]:
        if selected and case["id"] not in selected:
            continue
        repeats = repeats_override or case["repeats"]
        if case["kind"] == "central":
            for variant in case["variants"]:
                configurations.append((case, f"{case['id']}_{variant['name']}", repeats,
                                       ["--central", *config["common_central_args"], *variant["args"]], None))
            continue
        tol = tolerances[case["tolerance"]]
        for agents in case["agents"]:
            args = [*config["common_admm_args"], *case["args"], "--agents", str(agents),
                    "--iterations", str(case["budget"][agents]), "--timeout", str(max(600, 2 * case["budget"][agents])),
                    "--abs-tol", str(tol["abs"]), "--rel-tol", str(tol["rel"])]
            if "--batch" not in case["args"]:
                args += ["--batch", str(agents)]
            configurations.append((case, f"{case['id']}_k{agents}", repeats, args, agents))
    runs = []
    max_repeats = max((c[2] for c in configurations), default=0)
    for rep in range(1, max_repeats + 1):
        shift = (rep - 1) % max(1, len(configurations))
        rotated = configurations[shift:] + configurations[:shift]
        for case, name, repeats, args, agents in rotated:
            if rep <= repeats:
                runs.append({"case": case["id"], "name": f"{name}_r{rep}", "args": args, "agents": agents})
    return runs


def check_planned(planned_path, spec):
    """A finished run may only be skipped if it was started with exactly the planned arguments."""
    if not planned_path.exists():
        raise SystemExit(f"{planned_path.name} missing: cannot verify that the existing result belongs to this plan; "
                         "move the run away or use a new campaign directory")
    recorded = json.loads(planned_path.read_text(encoding="utf-8"))
    if recorded != spec:
        raise SystemExit(f"{planned_path.name} differs from the current plan (recorded {recorded}, planned {spec}); "
                         "results of a different configuration are never reused")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=ROOT / "cluster" / "case_studies.yaml")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--select", nargs="*", default=[], help="Case ids to run (default: all)")
    ap.add_argument("--repeats", type=int, help="Override repeats of every selected case")
    ap.add_argument("--hostfiles", type=Path, help="Directory with hosts_k<N>.txt (cluster mode)")
    ap.add_argument("--central-host", help="Worker for the central reference (cluster mode)")
    ap.add_argument("--local", action="store_true", help="Run on this machine (adds --scratch, --mpi-yield for 118 ranks)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.local == bool(args.hostfiles):
        raise SystemExit("Choose exactly one of --local or --hostfiles")
    config = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    runs = planned_runs(config, set(args.select), args.repeats)
    for run in runs:
        extra = []
        if args.local:
            extra = ["--scratch"] + (["--mpi-yield"] if run["agents"] == 118 else [])
        elif run["agents"] is None:
            if not args.central_host:
                raise SystemExit("Cluster mode needs --central-host for the central reference")
            extra = ["--central-host", args.central_host]
        else:
            hostfile = args.hostfiles / f"hosts_k{run['agents']}.txt"
            if not args.dry_run and not hostfile.exists():
                raise SystemExit(f"Missing hostfile {hostfile} (tools/make_hostfiles.py)")
            extra = ["--hostfile", str(hostfile)]
        run["extra"] = extra
        run["command"] = [sys.executable, str(ROOT / "tools" / "run_ac_local.py"), *run["args"], *extra,
                          "--out", str(args.out / run["name"])]
    if args.dry_run:
        for run in runs:
            print(" ".join(run["command"]))
        print(f"{len(runs)} runs planned")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    # One manifest per start; manifests of earlier starts of a resumed campaign are kept.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (args.out / f"campaign_manifest_{stamp}.json").write_text(json.dumps({
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "host": platform.node(),
        "commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "cases_file": str(args.cases), "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "selected": args.select, "mode": "local" if args.local else "cluster", "runs": runs}, indent=2), encoding="utf-8")
    log = args.out / "campaign.log"
    failed = []
    for index, run in enumerate(runs, 1):
        target = args.out / run["name"]
        spec = {"args": run["args"], "extra": run["extra"]}
        planned = args.out / f"{run['name']}.planned.json"
        if (target / "result.json").exists():
            check_planned(planned, spec)
            done = json.loads((target / "result.json").read_text())
            if not done.get("success"):
                failed.append(run["name"])
            print(f"[{index}/{len(runs)}] skip {run['name']} (done, success={done.get('success')})", flush=True)
            continue
        if target.exists():
            raise SystemExit(f"{target} exists without result.json (interrupted run): inspect and remove it before continuing")
        planned.write_text(json.dumps(spec), encoding="utf-8")
        start = time.perf_counter()
        with open(args.out / f"{run['name']}.driver.log", "w", encoding="utf-8") as stream:
            code = subprocess.run(run["command"], stdout=stream, stderr=subprocess.STDOUT).returncode
        result = json.loads((target / "result.json").read_text()) if (target / "result.json").exists() else {}
        line = (f"{datetime.now(timezone.utc).isoformat()} [{index}/{len(runs)}] {run['name']} exit={code} "
                f"success={result.get('success')} iterations={(result.get('admm') or {}).get('wall_iterations')} "
                f"wall={time.perf_counter() - start:.1f}s")
        print(line, flush=True)
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        if code != 0 or not result.get("success"):
            failed.append(run["name"])
    if failed:
        print(f"{len(failed)} of {len(runs)} runs not accepted: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
