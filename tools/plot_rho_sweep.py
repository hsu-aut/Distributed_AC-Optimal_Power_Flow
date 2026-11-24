"""Checkpoint diagnostics: actual strict ADMM gates, AC error and cost difference."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.ac_validation import validate_ac
from distributed_opf.config import load_topology_config


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep", type=Path)
    parser.add_argument("--central", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--label-by", choices=("rho", "agents", "segment"), default="rho")
    args = parser.parse_args()
    central_result = read(args.central / "result.json")
    if not central_result.get("success"):
        raise ValueError("Require an accepted central reference")
    reference = central_result["central"]
    curves = []
    solver_limits = set()
    for run in read(args.sweep / "sweep.json")["runs"]:
        folder = args.sweep / run["name"]
        manifest = read(folder / "manifest.json")
        if manifest["args"].get("central", False):
            continue
        solver_limits.add(float(manifest["args"]["solve_limit"]))
        if manifest["topology_sha256"] != reference["topology_sha256"]:
            raise ValueError("Reference topology mismatch")
        topology = load_topology_config(folder / "topology.yaml")
        points = []
        for checkpoint_dir in sorted((folder / "logs/checkpoints").glob("iter_*")):
            ranks = [read(p) for p in sorted(checkpoint_dir.glob("rank_*.json"))]
            if len(ranks) != manifest["args"]["agents"]:
                raise ValueError("Incomplete checkpoint")
            states, ratios = {}, []
            for rank in ranks:
                p = rank["params"]
                state = next(iter(rank["states"].values()))
                if rank["single_bus"]:
                    vectors = {k: list(state[k].values()) for k in ("uv", "uv_own", "uth", "uth_own")}
                else:
                    vectors = {k: [entry[2] for entry in rank["consensus"][source]]
                               for k, source in (("uv", "u_v"), ("uv_own", "u_v_own"), ("uth", "u_th"), ("uth_own", "u_th_own"))}
                dimension = max(1, 4 * len(vectors["uv"]))
                multiplier_norm = math.sqrt(sum((value * p["rho_v" if k.startswith("uv") else "rho_th"])**2
                                                for k, values in vectors.items() for value in values))
                eps_dual = math.sqrt(dimension)*p["abs_tol"] + p["rel_tol"]*multiplier_norm
                ratios.append((state["prim_res"]/p["abs_tol"], state["dual_res"]/eps_dual))
                for bus, value in rank["states"].items():
                    if int(bus) in states:
                        raise ValueError("Duplicate bus")
                    states[int(bus)] = value
            iteration = ranks[0]["iteration"]
            if any(rank["iteration"] != iteration for rank in ranks):
                raise ValueError("Mixed iterations")
            validation = validate_ac(topology, states)
            points.append(dict(iteration=iteration,
                primal_ratio=max(r[0] for r in ratios), dual_ratio=max(r[1] for r in ratios),
                balance_ratio=max(validation["max_dP"], validation["max_dQ"])/p["phys_tol"],
                cost_difference_pct=100*(validation["generation_cost"]-reference["generation_cost"])/abs(reference["generation_cost"]),
                feasible=validation["feasible"]))
        settings = manifest.get("settings", {})
        curves.append(dict(rho=manifest["args"]["rho"], agents=manifest["args"]["agents"], run=run["name"],
                           rho_v=float(settings.get("ADMM_RHO_V", manifest["args"]["rho"])),
                           rho_theta=float(settings.get("ADMM_RHO_THETA", manifest["args"]["rho"])), points=points))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "checkpoint_curves.json").write_text(json.dumps(curves, indent=2))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    panels = [("primal_ratio", "Primal residual / strict tolerance"),
              ("dual_ratio", "Dual residual / rank tolerance"),
              ("balance_ratio", "Maximum P/Q mismatch / tolerance"),
              ("cost_difference_pct", "Raw cost difference to central (%)")]
    for ax, (key, title) in zip(axes.flat, panels):
        for curve in curves:
            if args.label_by == "agents":
                label = f"{curve['agents']} partitions"
            elif curve["rho_v"] != curve["rho_theta"]:
                label = f"rho_v={curve['rho_v']:.0e}, rho_theta={curve['rho_theta']:.0e}"
            else:
                label = f"rho={curve['rho']:.0e}"
            if args.label_by == "segment":
                points = curve["points"]
                label = f"Checkpoints {points[0]['iteration']}–{points[-1]['iteration']}" if points else "No checkpoints"
            ax.plot([p["iteration"] for p in curve["points"]], [p[key] for p in curve["points"]],
                    marker=".", label=label)
        if key != "cost_difference_pct":
            ax.set_yscale("log")
            ax.axhline(1, color="black", linestyle="--", linewidth=.8)
        else:
            ax.set_yscale("symlog", linthresh=.01)
            ax.axhline(0, color="black", linestyle="--", linewidth=.8)
        ax.set_title(title)
        ax.set_xlabel("ADMM iteration")
        ax.grid(True, alpha=.2)
    axes[0, 0].legend()
    limit_label = "no local solver time limit" if solver_limits == {0.} else f"local solver limits (s): {sorted(solver_limits)}"
    comparison = f"ADMM partitions, rho={sorted({c['rho'] for c in curves})}" if args.label_by == "agents" else "fixed-rho ADMM"
    if args.label_by == "segment":
        comparison = f"ADMM continuation, agents={sorted({c['agents'] for c in curves})}, rho={sorted({c['rho'] for c in curves})}"
    fig.suptitle(f"Full IEEE118: {comparison}, {limit_label}\nCheckpoint samples; infeasible cost values are not solution quality")
    stem = "partition_diagnostics" if args.label_by == "agents" else "rho_diagnostics"
    if args.label_by == "segment":
        stem = "continuation_diagnostics"
    fig.savefig(args.out / f"{stem}.png", dpi=160)
    fig.savefig(args.out / f"{stem}.pdf")
    for curve in curves:
        print(json.dumps({"rho": curve["rho"], "agents": curve["agents"], "last": curve["points"][-1] if curve["points"] else None}))


if __name__ == "__main__":
    main()
