#!/usr/bin/env python3
"""Cluster preflight: one MPI rank per host checks that every node can run the study.

    mpirun --hostfile hosts.txt --map-by ppr:1:node python tools/cluster_preflight.py --topology topology_ieee118_full.yaml

Checks on every host: Python and package versions, Gurobi >= 13 with a working
license (solves a small nonconvex NLP with the local NL barrier exactly as the
agents do), identical SHA256 of all source files and the topology, CPU count,
memory, MPI library and point-to-point communication between all ranks.
Writes a JSON report on rank 0; exit code 0 only if all hosts are consistent.
Never prints license contents.
"""
import argparse
import hashlib
import json
import os
import platform
import socket
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpi4py import MPI  # noqa: E402


def package_versions():
    out = {}
    for name in ("numpy", "mpi4py", "gurobipy", "PyYAML"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def source_hashes(topology: Path):
    files = sorted((ROOT / "distributed_opf").glob("*.py")) + [ROOT / "run_mpi_cluster.py", ROOT / "central_ac_opf.py",
                                                               ROOT / "tools" / "run_ac_local.py", topology]
    return {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.exists()}


def memory_total_mib():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def gurobi_check():
    """Solve min (x-2)^2 + y s.t. y = sin(x) with the same local NL settings as the agents."""
    result = {"ok": False}
    try:
        import gurobipy as gp
        from gurobipy import GRB, nlfunc
        result["version"] = ".".join(map(str, gp.gurobi.version()))
        if gp.gurobi.version()[0] < 13:
            result["error"] = "Gurobi >= 13 required for local nonlinear optimization"
            return result
        start = time.perf_counter()
        with gp.Env(params={"OutputFlag": 0}) as env, gp.Model(env=env) as model:
            x = model.addVar(lb=-3, ub=3, name="x")
            y = model.addVar(lb=-1, ub=1, name="y")
            model.addGenConstrNL(y, nlfunc.sin(x))
            model.setObjective((x - 2) * (x - 2) + y, GRB.MINIMIZE)
            model.setParam("Threads", 1)
            model.setParam("NonConvex", 2)
            model.setParam("OptimalityTarget", 1)
            model.setParam("NLBarIterLimit", 1000)
            model.optimize()
            result.update(status=int(model.Status), solutions=int(model.SolCount),
                          objective=float(model.ObjVal) if model.SolCount else None)
            result["ok"] = model.Status in (GRB.OPTIMAL, GRB.LOCALLY_OPTIMAL) and model.SolCount > 0
        result["seconds"] = time.perf_counter() - start
    except Exception as exc:  # report, never raise: other hosts must still answer
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="topology_ieee118_full.yaml")
    ap.add_argument("--out", default="cluster_preflight.json")
    args = ap.parse_args()
    comm = MPI.COMM_WORLD
    rank, size = comm.Get_rank(), comm.Get_size()
    topology = (ROOT / args.topology).resolve()
    local = {"rank": rank, "hostname": socket.gethostname(), "platform": platform.platform(),
             "machine": platform.machine(), "python": platform.python_version(),
             "packages": package_versions(), "cpu_count": os.cpu_count(), "memory_mib": memory_total_mib(),
             "mpi_library": MPI.Get_library_version().strip().splitlines()[0],
             "license_file_set": bool(os.environ.get("GRB_LICENSE_FILE")) or (Path.home() / "gurobi.lic").exists(),
             "sources": source_hashes(topology), "gurobi": gurobi_check()}
    # Point-to-point ring: every rank must reach its successor and hear from its predecessor.
    token = comm.sendrecv(rank, dest=(rank + 1) % size, source=(rank - 1) % size)
    local["ring_ok"] = token == (rank - 1) % size
    reports = comm.gather(local, root=0)
    if rank != 0:
        return 0
    problems = []
    reference = reports[0]
    for report in reports:
        tag = f"rank {report['rank']} ({report['hostname']})"
        if not report["gurobi"]["ok"]:
            problems.append(f"{tag}: Gurobi check failed: {report['gurobi'].get('error') or report['gurobi'].get('status')}")
        if not report["ring_ok"]:
            problems.append(f"{tag}: MPI ring communication failed")
        for key in ("python", "packages", "mpi_library"):
            if report[key] != reference[key]:
                problems.append(f"{tag}: {key} differs from rank 0: {report[key]} vs {reference[key]}")
        if report["sources"] != reference["sources"]:
            differing = sorted(k for k in set(report["sources"]) | set(reference["sources"])
                               if report["sources"].get(k) != reference["sources"].get(k))
            problems.append(f"{tag}: source/topology hashes differ: {differing}")
        if not report["license_file_set"]:
            problems.append(f"{tag}: no GRB_LICENSE_FILE and no ~/gurobi.lic found")
    hosts = sorted({r["hostname"] for r in reports})
    summary = {"ranks": size, "hosts": len(hosts), "host_names": hosts, "ok": not problems,
               "problems": problems, "reports": reports}
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"preflight: {size} ranks on {len(hosts)} hosts, {'OK' if not problems else f'{len(problems)} problem(s)'}")
    for problem in problems:
        print("  -", problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
