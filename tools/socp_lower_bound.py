#!/usr/bin/env python3
"""Convex SOCP relaxation (Jabr) of the AC-OPF: numerically determined relaxation value.

    python tools/socp_lower_bound.py --topology topology_ieee118_full.yaml --upper 129660.69406511678 --out results/socp

For every branch with terminal admittances (Yff, Yft, Ytf, Ytt) of the MATPOWER pi model
(tap and phase shift included) the products of the complex voltages are replaced by

    u_i = |V_i|^2,  c_ij + j s_ij = V_i conj(V_j),   c_ij^2 + s_ij^2 <= u_i u_j   (rotated cone)

so that the terminal powers are linear:  S_i = conj(Yff) u_i + conj(Yft) (c + j s),
S_j = conj(Ytt) u_j + conj(Ytf) (c - j s). Bus balances with shunts, generator and voltage
bounds and (if present) thermal limits P^2 + Q^2 <= S_max^2 at both ends are kept.
Angle-difference limits and the cycle (angle) consistency are dropped, which only enlarges
the feasible set. The exact optimum of this convex problem is a lower bound on the global
optimum of the nonconvex AC-OPF. The solver returns only a numerical approximation (primal
objective at barrier termination); the value is therefore reported as a numerically determined
relaxation value and relaxation gap, not as a certified bound. A positive gap to a locally
optimal AC cost also does not show that the relaxation is inexact: the AC solution itself may
not be globally optimal.
"""
import argparse
import cmath
import json
import math
import sys
import time
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config  # noqa: E402


def terminal_admittances(line):
    y = 1.0 / complex(line.r_pu, line.x_pu)
    tap = line.tap_ratio * cmath.exp(1j * line.shift_rad)
    ytt = y + 0.5j * line.b_shunt_pu
    return ytt / abs(tap) ** 2, -y / tap.conjugate(), -y / tap, ytt  # Yff, Yft, Ytf, Ytt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="topology_ieee118_full.yaml")
    ap.add_argument("--upper", type=float, help="Cost of an accepted feasible AC solution")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--numeric-focus", type=int, default=0, help="Gurobi NumericFocus (0 = default)")
    args = ap.parse_args()
    topology = load_topology_config(ROOT / args.topology)
    model = gp.Model("socp_relaxation")
    model.Params.OutputFlag = 0
    model.Params.Threads = 1
    qcp_tol = 1e-8  # barrier QCP convergence tolerance (relative primal-dual gap and feasibility criteria)
    model.Params.BarQCPConvTol = qcp_tol
    model.Params.Crossover = 0
    model.Params.NumericFocus = args.numeric_focus

    u = {b: model.addVar(lb=bus.v_min_pu ** 2, ub=bus.v_max_pu ** 2, name=f"u_{b}") for b, bus in topology.buses.items()}
    pg, qg = {}, {}
    for b, bus in topology.buses.items():
        if bus.is_generator:
            pg[b] = model.addVar(lb=bus.pg_min, ub=bus.pg_max, name=f"pg_{b}")
            qg[b] = model.addVar(lb=bus.qg_min, ub=bus.qg_max, name=f"qg_{b}")
    p_out = {b: gp.LinExpr() for b in topology.buses}
    q_out = {b: gp.LinExpr() for b in topology.buses}
    rated = 0
    for e, line in enumerate(topology.lines):
        i, j = line.from_bus, line.to_bus
        yff, yft, ytf, ytt = terminal_admittances(line)
        c = model.addVar(lb=-GRB.INFINITY, name=f"c_{e}")
        s = model.addVar(lb=-GRB.INFINITY, name=f"s_{e}")
        model.addConstr(c * c + s * s <= u[i] * u[j], name=f"cone_{e}")
        # S_i = conj(Yff) u_i + conj(Yft) (c + j s);  S_j = conj(Ytt) u_j + conj(Ytf) (c - j s)
        a, bb = yft.conjugate().real, yft.conjugate().imag
        p_i = yff.conjugate().real * u[i] + a * c - bb * s
        q_i = yff.conjugate().imag * u[i] + bb * c + a * s
        a2, b2 = ytf.conjugate().real, ytf.conjugate().imag
        p_j = ytt.conjugate().real * u[j] + a2 * c + b2 * s
        q_j = ytt.conjugate().imag * u[j] + b2 * c - a2 * s
        p_out[i] += p_i; q_out[i] += q_i
        p_out[j] += p_j; q_out[j] += q_j
        limit = line.thermal_limit_pu
        if math.isfinite(limit):
            rated += 1
            for p_end, q_end, tag in ((p_i, q_i, "f"), (p_j, q_j, "t")):
                pe = model.addVar(lb=-GRB.INFINITY, name=f"p{tag}_{e}")
                qe = model.addVar(lb=-GRB.INFINITY, name=f"q{tag}_{e}")
                model.addConstr(pe == p_end)
                model.addConstr(qe == q_end)
                model.addConstr(pe * pe + qe * qe <= limit * limit, name=f"therm{tag}_{e}")
    cost = gp.QuadExpr()
    for b, bus in topology.buses.items():
        gen_p = pg.get(b, 0.0)
        gen_q = qg.get(b, 0.0)
        model.addConstr(gen_p - bus.demand_p == p_out[b] + bus.shunt_g_pu * u[b], name=f"pbal_{b}")
        model.addConstr(gen_q - bus.demand_q == q_out[b] - bus.shunt_b_pu * u[b], name=f"qbal_{b}")
        if bus.is_generator:
            cost += bus.cost_quad * pg[b] * pg[b] + bus.cost_lin * pg[b] + bus.cost_const
    model.setObjective(cost, GRB.MINIMIZE)
    start = time.perf_counter()
    model.optimize()
    elapsed = time.perf_counter() - start
    result = {"topology": args.topology, "status": int(model.Status), "solver_wall_s": elapsed,
              "rated_branches": rated, "numeric_focus": args.numeric_focus, "relaxation": "Jabr SOCP, angle limits and cycle constraints dropped"}
    if model.Status == GRB.OPTIMAL:
        objective = float(model.ObjVal)
        result.update(relaxation_objective=objective, barrier_qcp_convergence_tolerance=qcp_tol,
                      note=("numerically determined relaxation value (primal objective at barrier termination, "
                            "crossover off); not a certified lower bound"))
        if args.upper is not None:
            result.update(ac_cost=args.upper, relaxation_gap_pct=100 * (args.upper - objective) / args.upper)
    else:
        result.update(note="solver did not report OPTIMAL; no relaxation value reported")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "socp_lower_bound.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if model.Status == GRB.OPTIMAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
