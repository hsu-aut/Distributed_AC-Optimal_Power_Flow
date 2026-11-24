#!/usr/bin/env python3
"""Exact comparison of two raw-ADMM runs (implementation equivalence, resume identity).

Compares every agent log record present in both runs (same iteration) and the
common checkpoints. Numbers must be bit-identical; timing fields are ignored.
Exit code 0 only if everything compared is identical.
"""
import argparse
import json
from pathlib import Path

LOG_IGNORE = {"timing"}
CHECKPOINT_IGNORE = {"warm_start"}


def read_logs(run: Path):
    records = {}
    for path in sorted((run / "logs").glob("agent_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                key = (row["bus_id"], row["iteration"], bool(row.get("is_converged")), bool(row.get("is_best_iterate")))
                records[key] = {k: v for k, v in row.items() if k not in LOG_IGNORE}
    return records


def read_checkpoints(run: Path, ignore=()):
    out = {}
    for folder in sorted((run / "logs" / "checkpoints").glob("iter_*")):
        ranks = {}
        for p in sorted(folder.glob("rank_*.json")):
            payload = {k: v for k, v in json.loads(p.read_text()).items() if k not in CHECKPOINT_IGNORE and k not in ignore}
            # Checkpoints written before the edge-penalty variant existed are standard ADMM.
            if "edge_penalty" not in ignore:
                payload.setdefault("edge_penalty", None)
            ranks[p.name] = payload
        out[folder.name] = ranks
    return out


def diff(a, b, path="", out=None, limit=20):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                out.append(f"{path}/{key}: present only in {'first' if key in a else 'second'}")
            else:
                diff(a[key], b[key], f"{path}/{key}", out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for index, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{index}]", out, limit)
    elif a != b and not (isinstance(a, float) and isinstance(b, float) and a != a and b != b):
        extra = f" (|diff|={abs(a-b):.3e})" if isinstance(a, (int, float)) and isinstance(b, (int, float)) else ""
        out.append(f"{path}: {a!r} != {b!r}{extra}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("first", type=Path)
    ap.add_argument("second", type=Path)
    ap.add_argument("--min-iteration", type=int, default=0, help="Ignore log records before this iteration")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--ignore-checkpoint-key", action="append", default=[],
                    help="Top-level checkpoint key to exclude, e.g. edge_penalty when comparing a variant with f=1")
    args = ap.parse_args()
    logs_a, logs_b = read_logs(args.first), read_logs(args.second)
    common_logs = sorted(k for k in set(logs_a) & set(logs_b) if k[1] >= args.min_iteration)
    log_diffs = []
    for key in common_logs:
        diff(logs_a[key], logs_b[key], f"log{key}", log_diffs)
    ignored = tuple(args.ignore_checkpoint_key)
    ck_a, ck_b = read_checkpoints(args.first, ignored), read_checkpoints(args.second, ignored)
    common_ck = sorted(set(ck_a) & set(ck_b))
    ck_diffs = []
    for name in common_ck:
        diff(ck_a[name], ck_b[name], f"checkpoint/{name}", ck_diffs)
    summaries = []
    for run in (args.first, args.second):
        path = run / "logs" / "admm_run_summary.json"
        s = json.loads(path.read_text()) if path.exists() else {}
        summaries.append({k: s.get(k) for k in ("converged", "wall_iterations", "raw_ac_feasible", "last_phys_max_dP", "last_phys_max_dQ")})
    summary_diffs = diff(summaries[0], summaries[1], "summary")
    report = {"first": str(args.first), "second": str(args.second),
              "log_records_compared": len(common_logs), "checkpoints_compared": common_ck,
              "ignored_checkpoint_keys": sorted(CHECKPOINT_IGNORE | set(ignored)),
              "log_differences": log_diffs, "checkpoint_differences": ck_diffs,
              "summary_differences": summary_diffs, "summaries": summaries,
              "identical": bool(common_logs) and bool(common_ck) and not (log_diffs or ck_diffs or summary_diffs)}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
