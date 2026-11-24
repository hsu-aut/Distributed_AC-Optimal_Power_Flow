#!/usr/bin/env python3
"""
Zentrales AC-OPF mit Gurobi als Referenzlösung (im Vollmodell exakte nichtlineare
AC-Gleichungen, lokal optimal; PWL-Optionen nur für historische vereinfachte Fälle).

Ein einziges Modell über alle Busse und Leitungen — kein ADMM, keine Agenten-
Zerlegung. Vergleichbar mit dem verteilten Solver über dieselben Netzdaten und
dieselbe Flussphysik (pwl_ac), aber als monolithisches OPF.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import hashlib
from typing import Dict, Tuple

_script_dir = pathlib.Path(__file__).parent.absolute()
sys.path.insert(0, str(_script_dir))

import gurobipy as gp
from gurobipy import GRB

from distributed_opf.config import load_topology_config, TopologyConfig, line_admittance
from distributed_opf.admm_params import AdmmParams
from distributed_opf.pwl_ac import (
    AcPwlParams,
    add_apparent_power_polygon,
    add_branch_flow_pwl,
    configure_gurobi_quiet,
)


def _set_start(var: gp.Var, value: float) -> None:
    """Setze Start und klammere in Bounds (vermeidet 1e101-Undefined)."""
    if var is None:
        return
    lb = float(var.LB) if var.LB > -1e20 else -1e20
    ub = float(var.UB) if var.UB < 1e20 else 1e20
    var.Start = min(max(float(value), lb), ub)


def _env_bool(name: str, default: bool) -> bool:
    s = os.getenv(name)
    if s is None or str(s).strip() == "":
        return default
    v = str(s).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _use_central_balance_slack(bus, soft_balance: bool) -> bool:
    """
    Zentrales OPF: optional weiche Bilanz an *allen* Bussen (numerisch).
    Keine ADMM-Semantik (Last-only) — ein Modell sieht das gesamte Netz.
    """
    return bool(soft_balance)


def build_and_solve_central_acopf(
    yaml_path: str,
    pwl: AcPwlParams | None = None,
    verbose: bool = True,
) -> Tuple[
    Dict[int, float],
    Dict[int, float],
    Dict[int, float],
    Dict[int, float],
    Dict[Tuple[int, int], float],
    Dict[Tuple[int, int], float],
    float,
    Dict,
]:
    """Löse zentrales AC-OPF. Rückgabe: V, θ, pg, qg, pij, qij, obj, timing."""
    pwl = pwl or AcPwlParams.from_env()
    t_total_start = time.perf_counter()

    t_load_start = time.perf_counter()
    topo = load_topology_config(yaml_path)
    pwl.configure_topology(topo)
    t_load = time.perf_counter() - t_load_start

    t_build_start = time.perf_counter()
    model = gp.Model("central_ac_opf_pwl")
    configure_gurobi_quiet(model, verbose=verbose)

    buses = topo.buses
    lines = list(topo.lines)
    # Zentrales OPF: alle Leitungen inkl. Parallelzweige (eindeutiger Index)
    soft_balance = _env_bool("CENTRAL_SOFT_BALANCE", False)
    if _env_bool("CENTRAL_HARD_BALANCE", False):
        soft_balance = False
    if topo.full_ac_model and soft_balance:
        raise ValueError("Full AC OPF requires hard power balances")
    params = AdmmParams.from_env()

    v: Dict[int, gp.Var] = {}
    theta: Dict[int, gp.Var] = {}
    pg: Dict[int, gp.Var] = {}
    qg: Dict[int, gp.Var] = {}
    v_slack: Dict[int, Tuple[gp.Var, gp.Var]] = {}
    gen_slack: Dict[int, Tuple[gp.Var, gp.Var, gp.Var, gp.Var]] = {}
    bal_slack: Dict[int, Tuple[gp.Var, gp.Var]] = {}

    for bus_id, bus in buses.items():
        # Betriebsband hart; Slack-Bus: V=1, θ=0 (klassische Referenz)
        v_lo_b, v_hi_b = float(bus.v_min_pu), float(bus.v_max_pu)
        if bus.is_slack and abs(v_hi_b - v_lo_b) < 1e-6:
            v[bus_id] = model.addVar(lb=v_lo_b, ub=v_hi_b, name=f"V_{bus_id}")
        elif bus.is_slack and bus.fix_slack_voltage and v_lo_b <= 1.0 <= v_hi_b:
            v[bus_id] = model.addVar(lb=1.0, ub=1.0, name=f"V_{bus_id}")
        else:
            v[bus_id] = model.addVar(lb=v_lo_b, ub=v_hi_b, name=f"V_{bus_id}")
        svp = model.addVar(lb=0.0, name=f"sv_p_{bus_id}")
        svm = model.addVar(lb=0.0, name=f"sv_m_{bus_id}")
        if pwl.soft_voltage and not (bus.is_slack and abs(v_hi_b - v_lo_b) < 1e-6):
            model.addConstr(v[bus_id] <= bus.v_max_pu + svp, name=f"vub_{bus_id}")
            model.addConstr(-v[bus_id] <= -bus.v_min_pu + svm, name=f"vlb_{bus_id}")
        else:
            model.addConstr(v[bus_id] <= bus.v_max_pu, name=f"vub_{bus_id}")
            model.addConstr(-v[bus_id] <= -bus.v_min_pu, name=f"vlb_{bus_id}")
        v_slack[bus_id] = (svp, svm)

        if bus.is_slack:
            theta[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"theta_{bus_id}")
        else:
            th = 1.25 * float(pwl.theta_max)
            theta[bus_id] = model.addVar(lb=-th, ub=th, name=f"theta_{bus_id}")
        if bus.is_generator:
            pg[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pg_{bus_id}")
            qg[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qg_{bus_id}")
            spgp = model.addVar(lb=0.0, name=f"spgp_{bus_id}")
            spgm = model.addVar(lb=0.0, name=f"spgm_{bus_id}")
            sqgp = model.addVar(lb=0.0, name=f"sqgp_{bus_id}")
            sqgm = model.addVar(lb=0.0, name=f"sqgm_{bus_id}")
            model.addConstr(pg[bus_id] <= bus.pg_max + spgp, name=f"pgub_{bus_id}")
            model.addConstr(-pg[bus_id] <= -bus.pg_min + spgm, name=f"pglb_{bus_id}")
            model.addConstr(qg[bus_id] <= bus.qg_max + sqgp, name=f"qgub_{bus_id}")
            model.addConstr(-qg[bus_id] <= -bus.qg_min + sqgm, name=f"qglb_{bus_id}")
            gen_slack[bus_id] = (spgp, spgm, sqgp, sqgm)
            if not pwl.soft_generation:
                pg[bus_id].LB, pg[bus_id].UB = bus.pg_min, bus.pg_max
                qg[bus_id].LB, qg[bus_id].UB = bus.qg_min, bus.qg_max
                for slack in gen_slack[bus_id]:
                    slack.UB = 0.0
        else:
            pg[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"pg_{bus_id}")
            qg[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"qg_{bus_id}")

    # Key: (line_idx, from, to) — jede physikalische Leitung (auch Parallelzweige)
    p_ij: Dict[Tuple[int, int, int], gp.Var] = {}
    q_ij: Dict[Tuple[int, int, int], gp.Var] = {}
    therm_slack: Dict[int, gp.Var] = {}
    branch_aux: Dict[Tuple[int, int, int], dict] = {}
    directed_keys: list[Tuple[int, int, int]] = []

    for ell, line in enumerate(lines):
        i, j = line.from_bus, line.to_bus
        directed_keys.append((ell, i, j))
        directed_keys.append((ell, j, i))
        p_ij[(ell, i, j)] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pij_{ell}_{i}_{j}")
        q_ij[(ell, i, j)] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qij_{ell}_{i}_{j}")
        p_ij[(ell, j, i)] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pij_{ell}_{j}_{i}")
        q_ij[(ell, j, i)] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qij_{ell}_{j}_{i}")

    for ell, line in enumerate(lines):
        i, j = line.from_bus, line.to_bus
        g, b = line_admittance(line.r_pu, line.x_pu)
        bi, bj = buses[i], buses[j]
        vi_b = (float(bi.v_min_pu), float(bi.v_max_pu))
        vj_b = (float(bj.v_min_pu), float(bj.v_max_pu))
        branch_aux[(ell, i, j)] = add_branch_flow_pwl(
            model,
            v_i=v[i],
            v_j=v[j],
            theta_i=theta[i],
            theta_j=theta[j],
            p_ij=p_ij[(ell, i, j)],
            q_ij=q_ij[(ell, i, j)],
            g=g,
            b=b,
            b_shunt=line.b_shunt_pu,
            v_i_lb=vi_b[0],
            v_i_ub=vi_b[1],
            v_j_lb=vj_b[0],
            v_j_ub=vj_b[1],
            pwl=pwl,
            name=f"br_{ell}_{i}_{j}",
            line=line, from_bus=i,
        )
        branch_aux[(ell, j, i)] = add_branch_flow_pwl(
            model,
            v_i=v[j],
            v_j=v[i],
            theta_i=theta[j],
            theta_j=theta[i],
            p_ij=p_ij[(ell, j, i)],
            q_ij=q_ij[(ell, j, i)],
            g=g,
            b=b,
            b_shunt=line.b_shunt_pu,
            v_i_lb=vj_b[0],
            v_i_ub=vj_b[1],
            v_j_lb=vi_b[0],
            v_j_ub=vi_b[1],
            pwl=pwl,
            name=f"br_{ell}_{j}_{i}",
            line=line, from_bus=j,
        )
        smax = line.thermal_limit_pu
        s_ij = model.addVar(lb=0.0, name=f"s_therm_{ell}_{i}_{j}")
        s_ji = model.addVar(lb=0.0, name=f"s_therm_{ell}_{j}_{i}")
        if not pwl.soft_thermal:
            s_ij.UB = s_ji.UB = 0.0
        therm_slack[2 * ell] = s_ij
        therm_slack[2 * ell + 1] = s_ji
        add_apparent_power_polygon(
            model, p_ij[(ell, i, j)], q_ij[(ell, i, j)], smax, pwl.n_s_poly, f"S_{ell}_{i}_{j}", slack=s_ij
        )
        add_apparent_power_polygon(
            model, p_ij[(ell, j, i)], q_ij[(ell, j, i)], smax, pwl.n_s_poly, f"S_{ell}_{j}_{i}", slack=s_ji
        )

    for bus_id, bus in buses.items():
        p_sum = gp.LinExpr()
        q_sum = gp.LinExpr()
        for (ell, i, j) in directed_keys:
            if i == bus_id:
                p_sum += p_ij[(ell, i, j)]
                q_sum += q_ij[(ell, i, j)]
        use_bal = _use_central_balance_slack(bus, soft_balance)
        if use_bal:
            ps = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"ps_{bus_id}")
            qs = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qs_{bus_id}")
        else:
            ps = model.addVar(lb=0.0, ub=0.0, name=f"ps_{bus_id}")
            qs = model.addVar(lb=0.0, ub=0.0, name=f"qs_{bus_id}")
        bal_slack[bus_id] = (ps, qs)
        model.addConstr(pg[bus_id] - bus.demand_p == p_sum + bus.shunt_g_pu*v[bus_id]*v[bus_id] + ps, name=f"pbal_{bus_id}")
        model.addConstr(qg[bus_id] - bus.demand_q == q_sum - bus.shunt_b_pu*v[bus_id]*v[bus_id] + qs, name=f"qbal_{bus_id}")

    obj = gp.QuadExpr()
    for bus_id, bus in buses.items():
        if bus.is_generator:
            p = pg[bus_id]
            obj += bus.cost_quad * p * p + bus.cost_lin * p + bus.cost_const
            if bus_id in gen_slack:
                spgp, spgm, sqgp, sqgm = gen_slack[bus_id]
                wg = float(pwl.gen_slack_weight)
                obj += wg * (spgp * spgp + spgm * spgm + sqgp * sqgp + sqgm * sqgm)
        if pwl.soft_voltage:
            svp, svm = v_slack[bus_id]
            wv = float(pwl.v_slack_weight)
            obj += wv * (svp * svp + svm * svm)
        if _use_central_balance_slack(bus, soft_balance):
            ps, qs = bal_slack[bus_id]
            obj += float(params.p_slack_weight) * ps * ps
            if pwl.use_q_slack:
                obj += float(pwl.q_slack_weight) * qs * qs
    for s_slack in therm_slack.values():
        obj += pwl.line_slack_weight * s_slack * s_slack

    model.setObjective(obj, GRB.MINIMIZE)
    t_build = time.perf_counter() - t_build_start

    # Flat-Start inkl. Branch-Hilfsvariablen (sonst verwirft Gurobi den MIP-Start)
    model.update()
    v0s: Dict[int, float] = {}
    for bus_id, bus in buses.items():
        v0s[bus_id] = min(max(1.0, float(v[bus_id].LB)), float(v[bus_id].UB))
        _set_start(v[bus_id], v0s[bus_id])
        _set_start(theta[bus_id], 0.0)
        _set_start(pg[bus_id], 0.0)
        _set_start(qg[bus_id], 0.0)
        if bus_id in gen_slack:
            for s in gen_slack[bus_id]:
                _set_start(s, 0.0)
        if bus_id in v_slack:
            _set_start(v_slack[bus_id][0], 0.0)
            _set_start(v_slack[bus_id][1], 0.0)

    p_start: Dict[Tuple[int, int, int], float] = {}
    q_start: Dict[Tuple[int, int, int], float] = {}
    for ell, line in enumerate(lines):
        i, j = line.from_bus, line.to_bus
        g, b = line_admittance(line.r_pu, line.x_pu)
        for (ii, jj) in ((i, j), (j, i)):
            aux = branch_aux[(ell, ii, jj)]
            vi0 = v0s[ii]
            vj0 = v0s[jj]
            _set_start(aux.get("theta_ij"), 0.0)
            _set_start(aux.get("cos"), 1.0)
            _set_start(aux.get("sin"), 0.0)
            _set_start(aux.get("vi2"), vi0 * vi0)
            _set_start(aux.get("wij"), vi0 * vj0)
            _set_start(aux.get("wij_c"), vi0 * vj0)
            _set_start(aux.get("wij_s"), 0.0)
            p0 = g * vi0 * vi0 - g * vi0 * vj0
            q0 = -(b + 0.5 * line.b_shunt_pu) * vi0 * vi0 + b * vi0 * vj0
            _set_start(p_ij[(ell, ii, jj)], p0)
            _set_start(q_ij[(ell, ii, jj)], q0)
            p_start[(ell, ii, jj)] = p0
            q_start[(ell, ii, jj)] = q0
        _set_start(therm_slack[2 * ell], 0.0)
        _set_start(therm_slack[2 * ell + 1], 0.0)

    for bus_id, bus in buses.items():
        if bus_id not in bal_slack:
            continue
        p_sum0 = sum(p_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
        q_sum0 = sum(q_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
        _set_start(bal_slack[bus_id][0], -float(bus.demand_p) - p_sum0)
        _set_start(bal_slack[bus_id][1], -float(bus.demand_q) - q_sum0)

    # Bessere Start-Erzeugung: Demand proportional auf Generatoren
    gen_ids = [i for i, b in buses.items() if b.is_generator]
    pg_cap = {i: max(float(buses[i].pg_max), 1e-6) for i in gen_ids}
    cap_sum = sum(pg_cap.values())
    demand_p = sum(float(b.demand_p) for b in buses.values())
    demand_q = sum(float(b.demand_q) for b in buses.values())
    for i in gen_ids:
        share = pg_cap[i] / cap_sum
        _set_start(pg[i], share * demand_p)
        qmax = max(abs(float(buses[i].qg_max)), abs(float(buses[i].qg_min)), 1e-6)
        _set_start(qg[i], max(min(share * demand_q, float(buses[i].qg_max)), float(buses[i].qg_min)))
    # Bilanz-Slack neu: pg - d - p_sum
    for bus_id, bus in buses.items():
        if bus_id not in bal_slack:
            continue
        p_sum0 = sum(p_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
        q_sum0 = sum(q_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
        pg0 = float(pg[bus_id].Start) if pg[bus_id].Start < 1e100 else 0.0
        qg0 = float(qg[bus_id].Start) if qg[bus_id].Start < 1e100 else 0.0
        _set_start(bal_slack[bus_id][0], pg0 - float(bus.demand_p) - p_sum0)
        _set_start(bal_slack[bus_id][1], qg0 - float(bus.demand_q) - q_sum0)

    # Optional start point for the multi-start study of local optima (formulation unchanged).
    init_state_file = os.getenv("CENTRAL_INIT_STATE")
    if init_state_file:
        from distributed_opf.initialization import read_initial_state
        initial = read_initial_state(init_state_file)
        for bus_id in buses:
            _set_start(v[bus_id], initial[bus_id]["v"])
            _set_start(theta[bus_id], initial[bus_id]["va"])
            _set_start(pg[bus_id], initial[bus_id]["pg"])
            _set_start(qg[bus_id], initial[bus_id]["qg"])
        print(f"  Startpunkt aus {init_state_file}")

    # Optionaler Startpunkt aus ADMM-Logs (nur numerisch; Formulierung bleibt zentral)
    # Default: aus — zentrales OPF soll ohne ADMM laufen. Aktivieren: CENTRAL_ADMM_WARMSTART=1
    if _env_bool("CENTRAL_ADMM_WARMSTART", False):
        admm_dir = os.getenv("CENTRAL_ADMM_LOG_GLOB", "logs/agent_*.jsonl")
        try:
            import glob
            import json
            from pathlib import Path as _P

            admm = {}
            for path in glob.glob(admm_dir):
                r = json.loads(_P(path).read_text(encoding="utf-8").strip().splitlines()[-1])
                admm[int(r["bus_id"])] = r
            if len(admm) == len(buses):
                print(f"  optionaler ADMM-Warmstart aus {len(admm)} Agent-Logs")
                for bus_id, s in admm.items():
                    _set_start(v[bus_id], float(s["v"]))
                    _set_start(theta[bus_id], float(s.get("va", s.get("theta", 0.0))))
                    _set_start(pg[bus_id], float(s["pg"]))
                    _set_start(qg[bus_id], float(s["qg"]))
                for bus_id, bus in buses.items():
                    if bus_id not in bal_slack:
                        continue
                    p_sum0 = sum(p_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
                    q_sum0 = sum(q_start[(ell, i, j)] for (ell, i, j) in directed_keys if i == bus_id)
                    _set_start(bal_slack[bus_id][0], float(pg[bus_id].Start) - float(bus.demand_p) - p_sum0)
                    _set_start(bal_slack[bus_id][1], float(qg[bus_id].Start) - float(bus.demand_q) - q_sum0)
        except Exception as e:
            print(f"  ADMM-Warmstart übersprungen: {e}")

    # Alle übrigen Vars auf Midpoint, falls noch undefined
    model.update()
    for var in model.getVars():
        if var.Start >= 1e100:  # GRB.UNDEFINED
            mid = 0.0
            if var.LB > -1e20 and var.UB < 1e20:
                mid = 0.5 * (float(var.LB) + float(var.UB))
            elif var.LB > -1e20:
                mid = max(0.0, float(var.LB))
            elif var.UB < 1e20:
                mid = min(0.0, float(var.UB))
            _set_start(var, mid)

    # Zentrale Löser-Parameter
    time_limit = float(os.getenv("CENTRAL_TIME_LIMIT", os.getenv("GRB_TIMELIMIT", "900")))
    if time_limit == 0:
        time_limit = GRB.INFINITY
    mip_gap = float(os.getenv("CENTRAL_MIP_GAP", os.getenv("GRB_MIP_GAP", "1e-4")))
    phase1_frac = float(os.getenv("CENTRAL_PHASE1_FRAC", "0.35"))
    model.setParam(GRB.Param.MIPGap, mip_gap)
    model.setParam(GRB.Param.MIPFocus, 1)
    model.setParam(GRB.Param.DualReductions, 0)
    try:
        model.setParam(GRB.Param.Heuristics, 0.75)
        model.setParam(GRB.Param.FuncPieces, int(os.getenv("GRB_FUNC_PIECES", "12")))
        model.setParam(GRB.Param.FeasibilityTol, float(os.getenv("GRB_FEAS_TOL", "1e-5")))
    except Exception:
        pass

    print(f"Löse zentrales AC-OPF für {len(buses)} Busse und {len(lines)} Leitungen...")
    print(
        f"  mode=CENTRAL (monolithisch) n_bus={len(buses)} n_line={len(lines)} "
        f"exact_bilinear={pwl.exact_bilinear} native_trig={getattr(pwl, 'native_trig', False)} "
        f"soft_balance={soft_balance} TimeLimit={time_limit}s"
    )

    t_opt_start = time.perf_counter()
    # Solve the actual OPF objective directly; no feasibility-only phase.
    model.setObjective(obj, GRB.MINIMIZE)
    model.setParam(GRB.Param.TimeLimit, time_limit)
    local_target = int(model.Params.OptimalityTarget) == 1
    if local_target:
        model.update()
        for var in model.getVars():
            if abs(var.Start) < 1e90:
                var.PStart = var.Start
    model.optimize()
    t_optimize = time.perf_counter() - t_opt_start

    if model.SolCount <= 0:
        diag_dir = os.getenv("CENTRAL_DIAG_DIR")
        if diag_dir:
            diag_path = pathlib.Path(diag_dir)
            diag_path.mkdir(parents=True, exist_ok=True)
            model.write(str(diag_path / "model.lp"))
            if model.Status == GRB.INFEASIBLE:
                model.computeIIS()
                model.write(str(diag_path / "infeasible.ilp"))
        raise RuntimeError(
            f"Central ACOPF no solution, status={model.status}, SolCount=0, runtime={model.Runtime:.1f}s"
        )
    mip_gap_result = None if local_target else float(model.MIPGap)
    print(f"  status={model.Status} solcount={model.SolCount} gap={mip_gap_result}")

    t_extract_start = time.perf_counter()
    v_sol = {i: float(v[i].X) for i in v}
    theta_sol = {i: float(theta[i].X) for i in theta}
    pg_sol = {i: float(pg[i].X) for i in pg}
    qg_sol = {i: float(qg[i].X) for i in qg}
    # Speichere from->to; Parallelzweige als Summe
    p_sol: Dict[Tuple[int, int], float] = {}
    q_sol: Dict[Tuple[int, int], float] = {}
    for ell, ln in enumerate(lines):
        key = (ln.from_bus, ln.to_bus)
        p_sol[key] = p_sol.get(key, 0.0) + float(p_ij[(ell, ln.from_bus, ln.to_bus)].X)
        q_sol[key] = q_sol.get(key, 0.0) + float(q_ij[(ell, ln.from_bus, ln.to_bus)].X)
    # Generation cost only (ohne Slack-Penalty) für Vergleich
    gen_cost = 0.0
    for bus_id, bus in buses.items():
        if bus.is_generator:
            p = pg_sol[bus_id]
            gen_cost += bus.cost_quad * p * p + bus.cost_lin * p + bus.cost_const
    abs_ps = sum(abs(float(bal_slack[i][0].X)) for i in bal_slack)
    abs_qs = sum(abs(float(bal_slack[i][1].X)) for i in bal_slack)
    obj_val = float(model.ObjVal)
    t_extract = time.perf_counter() - t_extract_start
    t_total = time.perf_counter() - t_total_start

    timing = {
        "gurobi_runtime": float(model.Runtime),
        "gurobi_iterations": int(getattr(model, "IterCount", 0) or 0),
        "gurobi_status": int(model.Status),
        "mip_gap": mip_gap_result,
        "optimality_target": "local" if local_target else "global",
        "python_wall_time_load": t_load,
        "python_wall_time_build": t_build,
        "python_wall_time_optimize": t_optimize,
        "python_wall_time_extract": t_extract,
        "python_wall_time_total": t_total,
        "generation_cost": gen_cost,
        "abs_p_slack_sum": abs_ps,
        "abs_q_slack_sum": abs_qs,
        "soft_balance": soft_balance,
        "n_lines_modeled": len(lines),
        "formulation": "central_monolithic",
        "native_trig": bool(getattr(pwl, "native_trig", False)),
        "n_pwl_constraints": sum(c.GenConstrType == GRB.GENCONSTR_PWL for c in model.getGenConstrs()),
        "n_nonlinear_constraints": sum(c.GenConstrType == GRB.GENCONSTR_NL for c in model.getGenConstrs()),
        "n_sos_constraints": int(model.NumSOS),
        "n_integer_variables": int(model.NumIntVars),
        "solver_time_limit_s": None if model.Params.TimeLimit >= GRB.INFINITY else float(model.Params.TimeLimit),
    }
    print(
        f"Optimierung erfolgreich! Obj={obj_val:.6f}, GenCost={gen_cost:.6f}, "
        f"|ps|={abs_ps:.4f}, |qs|={abs_qs:.4f}"
    )
    return v_sol, theta_sol, pg_sol, qg_sol, p_sol, q_sol, obj_val, timing


def save_results(
    v_sol: Dict[int, float],
    theta_sol: Dict[int, float],
    pg_sol: Dict[int, float],
    qg_sol: Dict[int, float],
    p_sol: Dict[Tuple[int, int], float],
    q_sol: Dict[Tuple[int, int], float],
    obj_val: float,
    timing: Dict,
    topo: TopologyConfig,
    topology_file: str,
    output_dir: pathlib.Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    from distributed_opf.ac_validation import validate_ac
    validation = validate_ac(topo, {i: {"v": v_sol[i], "va": theta_sol[i],
                                      "pg": pg_sol[i], "qg": qg_sol[i]} for i in topo.buses})
    (output_dir / "ac_validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    results_json = {
        "mode": "ac_nonlinear_full" if topo.full_ac_model else "ac_pwl",
        "topology_file": str(topology_file),
        "topology_sha256": hashlib.sha256(pathlib.Path(topology_file).read_bytes()).hexdigest(),
        "base_mva": topo.base_mva,
        "objective": float(obj_val),
        "generation_cost": float(timing.get("generation_cost", obj_val)),
        "v": {str(k): float(val) for k, val in v_sol.items()},
        "theta": {str(k): float(val) for k, val in theta_sol.items()},
        "pg": {str(k): float(val) for k, val in pg_sol.items()},
        "qg": {str(k): float(val) for k, val in qg_sol.items()},
        "pij": {f"{i}_{j}": float(val) for (i, j), val in p_sol.items()},
        "qij": {f"{i}_{j}": float(val) for (i, j), val in q_sol.items()},
        "timing": timing,
        "raw_ac_feasible": validation["feasible"],
        "postprocessing": "none",
    }
    json_path = output_dir / "central_ac_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results_json, f, indent=2)
    print(f"Ergebnisse gespeichert: {json_path}")

    summary_path = output_dir / "central_ac_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("ZENTRALES AC-OPF\n")
        f.write(f"Topologie: {topology_file}\n")
        f.write(f"Objective: {obj_val:.6f}\n")
        f.write(f"Generation cost: {timing.get('generation_cost', 0):.6f}\n")
        f.write(f"Gurobi runtime: {timing.get('gurobi_runtime', 0):.4f}s\n\n")
        f.write("Busse (V, theta, pg, qg):\n")
        for bus_id in sorted(topo.buses.keys()):
            f.write(
                f"  {bus_id}: V={v_sol[bus_id]:.4f} θ={theta_sol[bus_id]:.4f} "
                f"Pg={pg_sol[bus_id]:.4f} Qg={qg_sol[bus_id]:.4f}\n"
            )
        f.write("\nLeitungen (P, Q from->to):\n")
        for (i, j), pval in p_sol.items():
            f.write(f"  {i}->{j}: P={pval:.4f} Q={q_sol[(i, j)]:.4f}\n")
    print(f"Zusammenfassung gespeichert: {summary_path}")
    if not validation["feasible"]:
        raise RuntimeError("Central candidate failed independent AC validation; see ac_validation.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Zentrales AC-OPF (Gurobi)")
    parser.add_argument("--topology", type=str, default="topology_3bus.yaml")
    parser.add_argument("--output-dir", type=str, default="central_opf_results")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    topology_path = pathlib.Path(args.topology)
    if not topology_path.exists():
        raise FileNotFoundError(f"Topologie-Datei nicht gefunden: {topology_path}")

    v_sol, theta_sol, pg_sol, qg_sol, p_sol, q_sol, obj_val, timing = build_and_solve_central_acopf(
        str(topology_path), verbose=not args.quiet
    )
    topo = load_topology_config(str(topology_path))
    save_results(
        v_sol,
        theta_sol,
        pg_sol,
        qg_sol,
        p_sol,
        q_sol,
        obj_val,
        timing,
        topo,
        str(topology_path),
        pathlib.Path(args.output_dir),
    )
    print(f"\nZentrale AC-OPF-Referenz erstellt in: {args.output_dir}")


if __name__ == "__main__":
    main()
