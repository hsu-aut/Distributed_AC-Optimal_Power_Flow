#!/usr/bin/env python3
"""Central DC-OPF on the same topology as an optional comparison to the full AC-OPF.

    python tools/dc_comparison.py --topology topology_ieee118_full.yaml \
        --ac-result results/benchmark_local_20260914/central_t1_r1_1/result.json --out results/dc_comparison

Standard lossless DC approximation (|V| = 1, sin(dθ) = dθ, r and shunt susceptances neglected):
branch flow (θ_i - θ_j - shift) / (x · tap), bus balance P_g - P_d - G_s = Σ flows, generator
P bounds, thermal limits |flow| ≤ S_max where rated, angle-difference limits where given, same
quadratic cost. Convex QP, solved to global optimality. The DC result is reported only as a
comparison (cost, dispatch, flows, active sets); it is never used as a start or to repair AC
results.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config  # noqa: E402

BOUND_TOL = 1e-6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="topology_ieee118_full.yaml")
    ap.add_argument("--ac-result", type=Path, required=True, help="Accepted central AC result.json")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    topology = load_topology_config(ROOT / args.topology)
    ac = json.loads(args.ac_result.read_text())
    if not ac.get("success"):
        raise SystemExit("AC result is not accepted")
    ac = ac["central"]

    model = gp.Model("dc_opf")
    model.Params.OutputFlag = 0
    model.Params.Threads = 1
    theta = {b: model.addVar(lb=-GRB.INFINITY, name=f"theta_{b}") for b in topology.buses}
    pg = {b: model.addVar(lb=bus.pg_min, ub=bus.pg_max, name=f"pg_{b}")
          for b, bus in topology.buses.items() if bus.is_generator}
    for b, bus in topology.buses.items():
        if bus.is_slack:
            model.addConstr(theta[b] == 0.0)
    out = {b: gp.LinExpr() for b in topology.buses}
    flows, thermal_constraints = [], []
    for e, line in enumerate(topology.lines):
        i, j = line.from_bus, line.to_bus
        flow = (theta[i] - theta[j] - line.shift_rad) * (1.0 / (line.x_pu * line.tap_ratio))
        out[i] += flow
        out[j] -= flow
        flows.append(flow)
        limit = line.thermal_limit_pu
        if math.isfinite(limit):
            thermal_constraints.append((e, model.addConstr(flow <= limit), model.addConstr(flow >= -limit)))
        if line.angle_min_rad is not None and line.angle_min_rad > -math.pi:
            model.addConstr(theta[i] - theta[j] >= line.angle_min_rad)
        if line.angle_max_rad is not None and line.angle_max_rad < math.pi:
            model.addConstr(theta[i] - theta[j] <= line.angle_max_rad)
    balance = {}
    cost = gp.QuadExpr()
    for b, bus in topology.buses.items():
        balance[b] = model.addConstr(pg.get(b, 0.0) - bus.demand_p - bus.shunt_g_pu == out[b])
        if bus.is_generator:
            cost += bus.cost_quad * pg[b] * pg[b] + bus.cost_lin * pg[b] + bus.cost_const
    model.setObjective(cost, GRB.MINIMIZE)
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        print(json.dumps({"status": int(model.Status)}))
        return 1

    dc_cost = float(model.ObjVal)
    ac_cost = float(ac["generation_cost"])
    buses = topology.buses
    gens = sorted(pg)
    dp = {b: pg[b].X - float(ac["pg"][str(b)]) for b in gens}
    slack = next(b for b, bus in buses.items() if bus.is_slack)
    ac_slack_theta = float(ac["theta"][str(slack)])  # both angle vectors referenced to the slack bus
    dtheta = [theta[b].X - (float(ac["theta"][str(b)]) - ac_slack_theta) for b in buses]
    ac_losses = sum(float(ac["pg"][str(b)]) for b in gens) - sum(bus.demand_p for bus in buses.values())

    def at_bound(value, lo, hi):
        return "min" if value <= lo + BOUND_TOL else ("max" if value >= hi - BOUND_TOL else None)

    dc_active = {b: at_bound(pg[b].X, buses[b].pg_min, buses[b].pg_max) for b in gens}
    ac_active = {b: at_bound(float(ac["pg"][str(b)]), buses[b].pg_min, buses[b].pg_max) for b in gens}
    dc_thermal = sorted(e for e, up, lo in thermal_constraints
                        if abs(up.Slack) <= BOUND_TOL or abs(lo.Slack) <= BOUND_TOL)
    result = {
        "topology": args.topology, "ac_result": str(args.ac_result).replace("\\", "/"),
        "dc_cost": dc_cost, "ac_cost": ac_cost,
        "cost_difference_pct": 100 * (dc_cost - ac_cost) / ac_cost,
        "ac_losses_pu": ac_losses,
        "dc_total_generation_pu": sum(pg[b].X for b in gens),
        "max_abs_pg_difference_pu": max(abs(v) for v in dp.values()),
        "sum_abs_pg_difference_pu": sum(abs(v) for v in dp.values()),
        "max_abs_theta_difference_rad": max(abs(v) for v in dtheta),
        "pg_bounds_active_dc": sum(v is not None for v in dc_active.values()),
        "pg_bounds_active_ac": sum(v is not None for v in ac_active.values()),
        "pg_active_sets_identical": dc_active == ac_active,
        "pg_active_set_differences": {str(b): {"dc": dc_active[b], "ac": ac_active[b]}
                                      for b in gens if dc_active[b] != ac_active[b]},
        "thermal_active_branches_dc": dc_thermal,
        "solver_status": int(model.Status),
        "note": "lossless DC approximation; comparison only, not an AC-feasible operating point",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "dc_comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
