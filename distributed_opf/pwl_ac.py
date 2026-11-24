"""AC branch-flow model with Gurobi, shared by the central reference and the ADMM subproblems.

The module name is historical: it began with piecewise-linear (PWL) approximations of the AC
equations. For the study case (`topology.full_ac_model`, e.g. topology_ieee118_full.yaml)
`AcPwlParams.configure_topology` forces the exact model: native nonlinear sin/cos, exact
bilinear products, exact thermal limits, no soft limits and no balance slacks. The PWL, McCormick
and soft-limit options remain only for the legacy simplified cases and are never active in the
full model (the central model statistics report 0 PWL, 0 SOS, 0 integer variables). The name is
kept unchanged so that source hashes in recorded manifests stay comparable.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import gurobipy as gp
from gurobipy import GRB


@dataclass
class AcPwlParams:
    """Parameter für die PWL-Approximation der AC-Gleichungen."""

    n_theta: int = 21
    theta_max: float = math.pi / 4
    n_s_poly: int = 8
    soft_thermal: bool = False
    soft_generation: bool = False
    exact_thermal: bool = True
    soft_voltage: bool = True
    # Weite harte Box nur numerisch; Betriebsgrenzen weich bestraft
    v_hard_min: float = 0.5
    v_hard_max: float = 1.5
    line_slack_weight: float = 1e5
    gen_slack_weight: float = 1e6
    v_slack_weight: float = 1e5
    use_q_slack: bool = True
    q_slack_weight: float = 1e5
    # Bilanz-Slack an jedem Bus für frühe ADMM-Machbarkeit (hohe Penalty → ~0 am Ende)
    balance_slack_all_buses: bool = True
    # True: bilineare Produkte exakt (NonConvex); False: McCormick-Relaxation
    exact_bilinear: bool = True
    # True: Gurobi GenConstrSin/Cos (kontinuierlich NonConvex); False: PWL/SOS-MIP
    native_trig: bool = False
    full_ac_model: bool = False

    def configure_topology(self, topology):
        if topology.full_ac_model:
            if os.getenv("AC_EXACT_THERMAL", "1").lower() in ("0", "false", "off"):
                raise ValueError("Full AC model requires exact thermal constraints")
            self.full_ac_model = True
            self.native_trig = self.exact_bilinear = self.exact_thermal = True
            self.soft_thermal = self.soft_generation = self.soft_voltage = False
            self.balance_slack_all_buses = self.use_q_slack = False
            # Every voltage phasor has a representative in [-pi, pi].
            self.theta_max = math.pi

    @classmethod
    def from_env(cls) -> "AcPwlParams":
        p = cls()

        def _int(name: str, default: int) -> int:
            try:
                s = os.getenv(name)
                return int(float(s)) if s is not None and s.strip() != "" else default
            except Exception:
                return default

        def _float(name: str, default: float) -> float:
            try:
                s = os.getenv(name)
                return float(s) if s is not None and s.strip() != "" else default
            except Exception:
                return default

        def _bool(name: str, default: bool) -> bool:
            s = os.getenv(name)
            if s is None:
                return default
            v = s.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
            return default

        p.n_theta = max(5, _int("AC_PWL_N", p.n_theta))
        p.theta_max = _float("AC_PWL_THETA_MAX", p.theta_max)
        p.n_s_poly = max(4, _int("AC_PWL_S_POLY", p.n_s_poly))
        p.soft_thermal = _bool("AC_SOFT_THERMAL", p.soft_thermal)
        p.soft_generation = _bool("AC_SOFT_GENERATION", p.soft_generation)
        p.exact_thermal = _bool("AC_EXACT_THERMAL", p.exact_thermal)
        p.soft_voltage = _bool("AC_SOFT_VOLTAGE", p.soft_voltage)
        p.v_hard_min = _float("AC_V_HARD_MIN", p.v_hard_min)
        p.v_hard_max = _float("AC_V_HARD_MAX", p.v_hard_max)
        p.line_slack_weight = _float("ADMM_LINE_SLACK_WEIGHT", p.line_slack_weight)
        p.gen_slack_weight = _float("ADMM_GEN_SLACK_WEIGHT", p.gen_slack_weight)
        p.v_slack_weight = _float("AC_V_SLACK_WEIGHT", p.v_slack_weight)
        p.use_q_slack = _bool("AC_USE_Q_SLACK", p.use_q_slack)
        p.q_slack_weight = _float("AC_Q_SLACK_WEIGHT", p.q_slack_weight)
        p.balance_slack_all_buses = _bool("AC_BALANCE_SLACK_ALL", p.balance_slack_all_buses)
        p.exact_bilinear = _bool("AC_EXACT_BILINEAR", p.exact_bilinear)
        p.native_trig = _bool("AC_NATIVE_TRIG", p.native_trig)
        return p


def theta_pwl_breakpoints(n: int, theta_max: float) -> Tuple[List[float], List[float], List[float]]:
    """Gleichmäßige Breakpoints für θ, cos(θ), sin(θ)."""
    n = max(3, int(n))
    xs = [-theta_max + 2.0 * theta_max * i / (n - 1) for i in range(n)]
    cos_y = [math.cos(x) for x in xs]
    sin_y = [math.sin(x) for x in xs]
    return xs, cos_y, sin_y


def add_mccormick(
    model: gp.Model,
    x: gp.Var,
    y: gp.Var,
    z: gp.Var,
    x_lb: float,
    x_ub: float,
    y_lb: float,
    y_ub: float,
    name: str,
) -> None:
    """McCormick-Envelope für z ≈ x * y."""
    model.addConstr(z >= x_lb * y + y_lb * x - x_lb * y_lb, name=f"{name}_mc1")
    model.addConstr(z >= x_ub * y + y_ub * x - x_ub * y_ub, name=f"{name}_mc2")
    model.addConstr(z <= x_lb * y + y_ub * x - x_lb * y_ub, name=f"{name}_mc3")
    model.addConstr(z <= x_ub * y + y_lb * x - x_ub * y_lb, name=f"{name}_mc4")


def add_apparent_power_polygon(
    model: gp.Model,
    p: gp.Var,
    q: gp.Var,
    smax: float,
    n_sides: int,
    name: str,
    slack: gp.Var | None = None,
) -> List[gp.Constr]:
    """Exact thermal circle by default; polygon only when explicitly requested."""
    if math.isinf(smax):
        return []
    bounds = getattr(model, "_ac_flow_bounds", {})
    p_bound, q_bound = bounds.get(p.index), bounds.get(q.index)
    if p_bound is not None and q_bound is not None and math.hypot(p_bound, q_bound) <= smax:
        # Provably redundant over the entire voltage box, not a tolerance.
        # Avoid squaring unlimited-rating sentinels (e.g. 1e7 p.u.).
        return []
    if os.getenv("AC_EXACT_THERMAL", "1").lower() not in ("0", "false", "off"):
        rhs = smax if slack is None else smax + slack
        return [model.addQConstr(p*p + q*q <= rhs*rhs, name=f"{name}_circle")]
    constrs: List[gp.Constr] = []
    n = max(4, int(n_sides))
    for k in range(n):
        ang = 2.0 * math.pi * k / n
        # cos*P + sin*Q ≤ Smax (+ slack)
        lhs = math.cos(ang) * p + math.sin(ang) * q
        if slack is not None:
            constrs.append(model.addConstr(lhs <= smax + slack, name=f"{name}_poly_{k}"))
        else:
            constrs.append(model.addConstr(lhs <= smax, name=f"{name}_poly_{k}"))
    return constrs


def branch_dtheta_bounds(
    model: gp.Model,
    theta_i: gp.Var,
    theta_j: gp.Var,
    pwl: AcPwlParams,
) -> Tuple[float, float, float]:
    """Return (dth_lo, dth_hi, pwl_span) from bus-theta limits (no artificial clip)."""
    try:
        model.update()
    except Exception:
        pass
    try:
        th_i_lo, th_i_hi = float(theta_i.LB), float(theta_i.UB)
        th_j_lo, th_j_hi = float(theta_j.LB), float(theta_j.UB)
    except (AttributeError, gp.GurobiError):
        th = 1.25 * float(pwl.theta_max)
        th_i_lo = th_j_lo = -th
        th_i_hi = th_j_hi = th
    dth_lo = th_i_lo - th_j_hi
    dth_hi = th_i_hi - th_j_lo
    if dth_lo > dth_hi:
        dth_lo, dth_hi = dth_hi, dth_lo
    span = max(abs(dth_lo), abs(dth_hi), float(pwl.theta_max), 1e-9)
    span = min(span, math.pi)
    return dth_lo, dth_hi, span


def add_pwl_trig(
    model: gp.Model,
    theta: gp.Var,
    cos_var: gp.Var,
    sin_var: gp.Var,
    pwl: AcPwlParams,
    name: str,
    *,
    theta_span: float | None = None,
) -> None:
    """Trig: native GenConstrSin/Cos oder PWL-Approximation."""
    if getattr(pwl, "native_trig", False):
        model.addGenConstrCos(theta, cos_var, name=f"{name}_cos", options="FuncNonlinear=1")
        model.addGenConstrSin(theta, sin_var, name=f"{name}_sin", options="FuncNonlinear=1")
        return
    span = float(theta_span if theta_span is not None else pwl.theta_max)
    xs, cos_y, sin_y = theta_pwl_breakpoints(pwl.n_theta, span)
    model.addGenConstrPWL(theta, cos_var, xs, cos_y, name=f"{name}_cos")
    model.addGenConstrPWL(theta, sin_var, xs, sin_y, name=f"{name}_sin")


def add_branch_flow_pwl(
    model: gp.Model,
    *,
    v_i: gp.Var,
    v_j: gp.Var,
    theta_i: gp.Var,
    theta_j: gp.Var,
    p_ij: gp.Var,
    q_ij: gp.Var,
    g: float,
    b: float,
    b_shunt: float,
    v_i_lb: float,
    v_i_ub: float,
    v_j_lb: float,
    v_j_ub: float,
    pwl: AcPwlParams,
    name: str,
    line=None,
    from_bus=None,
) -> dict:
    """
    PWL/McCormick-AC-Fluss von i nach j:

      Pij = Vi² g - Vi Vj (g cos + b sin)
      Qij = -Vi² (b + bsh/2) - Vi Vj (g sin - b cos)
    """
    ys, ym = complex(g, b+b_shunt/2), complex(-g, -b)
    if line is not None:
        from .config import terminal_admittance
        ys, ym = terminal_admittance(line, from_bus)
        angle = theta_i-theta_j if from_bus == line.from_bus else theta_j-theta_i
        if line.angle_min_rad is not None:
            model.addConstr(angle >= line.angle_min_rad, name=f"{name}_angmin")
        if line.angle_max_rad is not None:
            model.addConstr(angle <= line.angle_max_rad, name=f"{name}_angmax")
    dth_lo, dth_hi, pwl_span = branch_dtheta_bounds(model, theta_i, theta_j, pwl)
    if (pwl.full_ac_model or os.getenv("AC_DIRECT_NL", "0") == "1") and pwl.native_trig and pwl.exact_bilinear:
        from gurobipy import nlfunc
        dt = theta_i - theta_j
        c, s = nlfunc.cos(dt), nlfunc.sin(dt)
        model.addGenConstrNL(p_ij, ys.real*v_i*v_i + v_i*v_j*(ym.real*c+ym.imag*s), name=f"{name}_pflow")
        model.addGenConstrNL(q_ij, -ys.imag*v_i*v_i + v_i*v_j*(ym.real*s-ym.imag*c), name=f"{name}_qflow")
        if not hasattr(model, "_ac_flow_bounds"):
            model._ac_flow_bounds = {}
        vv_max = v_i_ub*v_j_ub
        model._ac_flow_bounds[p_ij.index] = abs(ys.real)*v_i_ub**2 + abs(ym)*vv_max
        model._ac_flow_bounds[q_ij.index] = abs(ys.imag)*v_i_ub**2 + abs(ym)*vv_max
        return {}
    theta_ij = model.addVar(lb=dth_lo, ub=dth_hi, name=f"{name}_dtheta")
    model.addConstr(theta_ij == theta_i - theta_j, name=f"{name}_dtheta_def")

    if getattr(pwl, "native_trig", False):
        # Native sin/cos: volle Trig-Bounds (Bus-θ kann ±1.25·theta_max sein)
        cos_lb, cos_ub = -1.0, 1.0
        sin_lb, sin_ub = -1.0, 1.0
    else:
        xs, cos_ys, sin_ys = theta_pwl_breakpoints(pwl.n_theta, pwl_span)
        cos_lb, cos_ub = min(cos_ys), max(cos_ys)
        sin_lb, sin_ub = min(sin_ys), max(sin_ys)

    cos_ij = model.addVar(lb=cos_lb, ub=cos_ub, name=f"{name}_cos")
    sin_ij = model.addVar(lb=sin_lb, ub=sin_ub, name=f"{name}_sin")
    add_pwl_trig(model, theta_ij, cos_ij, sin_ij, pwl, name, theta_span=pwl_span)

    # Vi² und Vi·Vj: Betriebsband für McCormick; optional exakt (NonConvex)
    vi2_lb = v_i_lb * v_i_lb
    vi2_ub = v_i_ub * v_i_ub
    vi2 = model.addVar(lb=vi2_lb, ub=vi2_ub, name=f"{name}_vi2")
    wij_lb = min(v_i_lb * v_j_lb, v_i_lb * v_j_ub, v_i_ub * v_j_lb, v_i_ub * v_j_ub)
    wij_ub = max(v_i_lb * v_j_lb, v_i_lb * v_j_ub, v_i_ub * v_j_lb, v_i_ub * v_j_ub)
    wij = model.addVar(lb=wij_lb, ub=wij_ub, name=f"{name}_wij")
    c_products = [w*c for w in (wij_lb, wij_ub) for c in (cos_lb, cos_ub)]
    s_products = [w*s for w in (wij_lb, wij_ub) for s in (sin_lb, sin_ub)]
    wij_c = model.addVar(lb=min(c_products), ub=max(c_products), name=f"{name}_wij_c")
    wij_s = model.addVar(lb=min(s_products), ub=max(s_products), name=f"{name}_wij_s")

    if getattr(pwl, "exact_bilinear", True):
        model.addConstr(vi2 == v_i * v_i, name=f"{name}_vi2_eq")
        model.addConstr(wij == v_i * v_j, name=f"{name}_wij_eq")
        model.addConstr(wij_c == wij * cos_ij, name=f"{name}_wij_c_eq")
        model.addConstr(wij_s == wij * sin_ij, name=f"{name}_wij_s_eq")
    else:
        # McCormick nur mit Betriebsband [v_min, v_max], nie mit [0.5, 1.5]
        add_mccormick(model, v_i, v_i, vi2, v_i_lb, v_i_ub, v_i_lb, v_i_ub, f"{name}_vi2")
        add_mccormick(model, v_i, v_j, wij, v_i_lb, v_i_ub, v_j_lb, v_j_ub, f"{name}_wij")
        add_mccormick(model, wij, cos_ij, wij_c, wij_lb, wij_ub, cos_lb, cos_ub, f"{name}_wij_c")
        add_mccormick(model, wij, sin_ij, wij_s, wij_lb, wij_ub, sin_lb, sin_ub, f"{name}_wij_s")

    b_tot = b + 0.5 * b_shunt
    if not hasattr(model, "_ac_flow_bounds"):
        model._ac_flow_bounds = {}
    vv_max = v_i_ub * v_j_ub
    model._ac_flow_bounds[p_ij.index] = abs(ys.real)*vi2_ub + abs(ym)*vv_max
    model._ac_flow_bounds[q_ij.index] = abs(ys.imag)*vi2_ub + abs(ym)*vv_max
    model.addConstr(
        p_ij == ys.real * vi2 + ym.real * wij_c + ym.imag * wij_s,
        name=f"{name}_pflow",
    )
    model.addConstr(
        q_ij == -ys.imag * vi2 + ym.real * wij_s - ym.imag * wij_c,
        name=f"{name}_qflow",
    )

    return {
        "theta_ij": theta_ij,
        "cos": cos_ij,
        "sin": sin_ij,
        "vi2": vi2,
        "wij": wij,
        "wij_c": wij_c,
        "wij_s": wij_s,
    }


def configure_gurobi_quiet(model: gp.Model, verbose: bool = False) -> None:
    model.setParam(GRB.Param.OutputFlag, 1 if verbose else 0)
    try:
        model.setParam(GRB.Param.LogToConsole, 1 if verbose else 0)
    except Exception:
        pass
    threads = 1
    try:
        t = os.getenv("GRB_THREADS")
        if t is not None and t.strip() != "":
            threads = max(0, int(float(t)))
    except Exception:
        threads = 1
    # 0 keeps Gurobi's default (all available cores).
    model.setParam(GRB.Param.Threads, threads)
    # PWL/SOS / bilineare Produkte brauchen NonConvex
    model.setParam(GRB.Param.NonConvex, 2)
    model.setParam(GRB.Param.FeasibilityTol, float(os.getenv("GRB_FEAS_TOL", "1e-8")))
    model.setParam(GRB.Param.MIPGap, float(os.getenv("GRB_MIP_GAP", "1e-4")))
    model.setParam(GRB.Param.MIPGapAbs, float(os.getenv("GRB_MIP_GAP_ABS", "1e-8")))
    if os.getenv("GRB_OPTIMALITY_TARGET", "global") == "local":
        if gp.gurobi.version()[0] < 13:
            raise RuntimeError("Local nonlinear optimization requires Gurobi >= 13")
        model.setParam("OptimalityTarget", 1)
        model.setParam("NLBarIterLimit", int(os.getenv("GRB_NL_ITER_LIMIT", "1000")))
        model.setParam("NLBarPFeasTol", 1e-9)
        model.setParam("NLBarDFeasTol", 1e-9)
        model.setParam("NLBarCFeasTol", 1e-9)
    try:
        # INF vs UNBD klarer; hilft bei ADMM-Nachbar-Fixes
        model.setParam(GRB.Param.DualReductions, 0)
    except Exception:
        pass
    try:
        tl = os.getenv("GRB_TIMELIMIT")
        if tl is not None and str(tl).strip() != "":
            tlf = float(tl)
            if tlf > 0:
                model.setParam(GRB.Param.TimeLimit, tlf)
    except Exception:
        pass
