#!/usr/bin/env python3
"""Regenerate all tables and figures of the thesis from the recorded run directories.

    python tools/reproduce_figures.py --results results --out results/thesis_figures

Each entry of TARGETS names the experiment folders it reads and the analysis tool it runs.
Folders under results/ are local (not versioned); compact evidence of every experiment is in
validation/ (see validation/INDEX.md). Missing folders are reported and skipped, so the
script can be run on a fresh clone after copying the relevant results.
The script only reads run data and writes new files below --out.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name -> (tool, list of result folders relative to --results, extra arguments[, allow_rejected])
# allow_rejected: the series deliberately contains non-converged runs (e.g. rho bracket), so the
# aggregation exit code 1 ("not all accepted") is expected and not a reproduction failure.
TARGETS = {
    "benchmark_local": ("thesis_figures.py", ["benchmark_local_20260914"], []),
    "benchmark_aggregate": ("aggregate_benchmarks.py", ["benchmark_local_20260914"], []),
    "limits_original_case": ("limits_utilization.py", ["benchmark_local_20260914"], []),
    "limits_initialization": ("limits_utilization.py", ["init_study_20260915"], []),
    "limits_pglib_variant": ("limits_utilization.py", ["pglib_limits_20260915"], []),
    "rho_sensitivity": ("aggregate_benchmarks.py", ["rho_sensitivity_20260915", "benchmark_local_20260914"], [], True),
    "initialization": ("aggregate_benchmarks.py", ["init_study_20260915"], [], True),  # 118 pilot capped at 3000
    "campaign_local_tol1e4": ("thesis_figures.py", ["local_campaign_tol1e4_20260915/runs"], []),
    "campaign_local_tol1e4_aggregate": ("aggregate_benchmarks.py", ["local_campaign_tol1e4_20260915/runs"], []),
    "bus118_edge_repeat": ("aggregate_benchmarks.py", ["bus118_edge_strict_repeat_20260915"], []),
    "edge_rho_robustness": ("aggregate_benchmarks.py", ["edge_rho_robustness_20260915"], [], True),
}

# Central analyses without run folders: name -> (tool, arguments relative to the repository,
# result files relative to --results that must exist).
CENTRAL_TARGETS = {
    "socp_original": ("socp_lower_bound.py", ["--topology", "topology_ieee118_full.yaml",
                      "--upper", "129660.69406511678", "--numeric-focus", "3"], []),
    "socp_pglib": ("socp_lower_bound.py", ["--topology", "topology_ieee118_pglib.yaml",
                   "--upper", "97213.60739682875", "--numeric-focus", "3"], []),
    "dc_original": ("dc_comparison.py", ["--topology", "topology_ieee118_full.yaml", "--ac-result",
                    "{results}/benchmark_local_20260914/central_t1_r1_1/result.json"],
                    ["benchmark_local_20260914/central_t1_r1_1/result.json"]),
    "dc_pglib": ("dc_comparison.py", ["--topology", "topology_ieee118_pglib.yaml", "--ac-result",
                 "{results}/pglib_limits_20260915/central_t1/result.json"],
                 ["pglib_limits_20260915/central_t1/result.json"]),
    "criterion_comparison": ("criterion_comparison.py",
                             [f"{{results}}/criterion_check_20260915/{run}" for run in ("p3", "p4", "p8", "p16", "bus118_edge")]
                             + ["--levels", "1e-4"],
                             [f"criterion_check_20260915/{run}/result.json" for run in ("p3", "p4", "p8", "p16", "bus118_edge")]),
    "criterion_comparison_partitions": ("criterion_comparison.py",
                                        [f"{{results}}/criterion_check_20260915/{run}" for run in ("p3", "p4", "p8", "p16")]
                                        + ["--levels", "1e-4", "3e-5", "1e-5"],
                                        [f"criterion_check_20260915/{run}/result.json" for run in ("p3", "p4", "p8", "p16")]),
}

# Budget and tolerance levels need individual run folders with checkpoint histories.
BUDGET_RUNS = [
    "phase5_partitions_20260914/ci_p3", "phase5_partitions_20260914/ci_p4",
    "phase5_partitions_20260914/ci_p4_resume15000", "phase5_partitions_20260914/ci_p8",
    "phase5_partitions_20260914/ci_p16", "phase5_partitions_20260914/ci_p16_resume25000",
    "edge_rho_20260915/bus118_edge_p1_rho1e6_long", "edge_rho_20260915/bus118_edge_p1_rho1e6_resume70000",
]


def run(tool, arguments):
    command = [sys.executable, str(ROOT / "tools" / tool), *arguments]
    completed = subprocess.run(command, capture_output=True, text=True)
    return {"command": command, "exit_code": completed.returncode,
            "stdout_tail": completed.stdout.strip().splitlines()[-5:], "stderr_tail": completed.stderr.strip().splitlines()[-5:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, (tool, folders, extra, *flags) in TARGETS.items():
        if args.only and name not in args.only:
            continue
        paths = [args.results / f for f in folders]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            report[name] = {"skipped": True, "missing": missing}
            print(f"skip {name}: missing {missing}")
            continue
        report[name] = run(tool, [*map(str, paths), "--out", str(args.out / name), *extra])
        # Only the aggregation verdict may be excused, never a crash: the summary must exist.
        if flags and flags[0] and report[name]["exit_code"] == 1 and (args.out / name / "summary.json").exists():
            report[name]["rejected_runs_expected"] = True
            report[name]["exit_code"] = 0
        print(f"{name}: exit {report[name]['exit_code']}")
    for name, (tool, arguments, required) in CENTRAL_TARGETS.items():
        if args.only and name not in args.only:
            continue
        missing = [str(args.results / r) for r in required if not (args.results / r).exists()]
        if missing:
            report[name] = {"skipped": True, "missing": missing}
            print(f"skip {name}: missing {missing}")
            continue
        arguments = [a.replace("{results}", str(args.results)) for a in arguments]
        report[name] = run(tool, [*arguments, "--out", str(args.out / name)])
        print(f"{name}: exit {report[name]['exit_code']}")
    if not args.only or "iteration_budget" in args.only:
        paths = [args.results / r for r in BUDGET_RUNS]
        present = [str(p) for p in paths if (p / "manifest.json").exists()]
        if present:
            report["iteration_budget"] = run("iteration_budget.py", [*present, "--out", str(args.out / "iteration_budget")])
            print(f"iteration_budget: exit {report['iteration_budget']['exit_code']} ({len(present)} runs)")
        else:
            report["iteration_budget"] = {"skipped": True, "missing": [str(p) for p in paths]}
    (args.out / "reproduce_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    failed = [n for n, r in report.items() if r.get("exit_code") not in (0, None)]
    print("failed:", failed or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
