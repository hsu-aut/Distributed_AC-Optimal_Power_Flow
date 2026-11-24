"""Solver-independent validation of the complete, unmodified AC candidate.

All powers use the topology's p.u. base; angles are radians. No power-flow
solve, clipping, missing-value recovery or other change to the candidate.
"""
from dataclasses import dataclass, asdict
import math
import cmath
from .config import line_admittance


@dataclass(frozen=True)
class AcTolerances:
    balance: float = 1e-4
    voltage: float = 1e-5
    generation: float = 1e-5
    thermal: float = 1e-5
    angle: float = 1e-5


def validate_ac(topo, states, tolerances=None, theta_max=math.pi / 4):
    tol = tolerances or AcTolerances()
    if any(not math.isfinite(v) or v <= 0 for v in asdict(tol).values()):
        raise ValueError("All AC validation tolerances must be finite and positive")
    expected = set(topo.buses)
    errors = []
    if set(states) != expected:
        errors.append(f"bus coverage mismatch: missing={sorted(expected-set(states))}, extra={sorted(set(states)-expected)}")
    values = {}
    for bid, st in states.items():
        try:
            rec = {k: float(st[k] if isinstance(st, dict) else getattr(st, k))
                   for k in ("v", "va", "pg", "qg")}
            if not all(math.isfinite(v) for v in rec.values()):
                raise ValueError("nonfinite state")
            values[bid] = rec
        except (KeyError, AttributeError, ValueError, TypeError) as exc:
            errors.append(f"bus {bid}: {exc}")
    if errors:
        return {"valid_data": False, "feasible": False, "errors": errors,
                "tolerances": asdict(tol)}
    injections = {b: [topo.buses[b].shunt_g_pu*values[b]["v"]**2,
                       -topo.buses[b].shunt_b_pu*values[b]["v"]**2] for b in expected}
    branches = []
    branch_angle_violation = 0.0
    for ell, ln in enumerate(topo.lines):
        vf = cmath.rect(values[ln.from_bus]["v"], values[ln.from_bus]["va"])
        vt = cmath.rect(values[ln.to_bus]["v"], values[ln.to_bus]["va"])
        tap = cmath.rect(ln.tap_ratio, ln.shift_rad)
        series_current = (vf/tap-vt)/complex(ln.r_pu, ln.x_pu)
        sf = vf*((series_current + 0.5j*ln.b_shunt_pu*vf/tap)/tap.conjugate()).conjugate()
        st = vt*(-series_current + 0.5j*ln.b_shunt_pu*vt).conjugate()
        delta = values[ln.from_bus]["va"]-values[ln.to_bus]["va"]
        branch_angle_violation = max(branch_angle_violation,
            (ln.angle_min_rad-delta) if ln.angle_min_rad is not None else 0.,
            (delta-ln.angle_max_rad) if ln.angle_max_rad is not None else 0.)
        limit = ln.thermal_limit_pu
        for i, j, flow in ((ln.from_bus, ln.to_bus, sf), (ln.to_bus, ln.from_bus, st)):
            p, q = flow.real, flow.imag
            injections[i][0] += p
            injections[i][1] += q
            branches.append({"branch_id": ell, "from": i, "to": j, "p": p, "q": q,
                             "s": math.hypot(p,q), "limit": limit if math.isfinite(limit) else None,
                             "violation": max(0., math.hypot(p,q)-limit)})
    rows = []
    cost = 0.
    for bid, bus in topo.buses.items():
        st = values[bid]
        if bus.is_generator:
            gp = max(0., bus.pg_min-st["pg"], st["pg"]-bus.pg_max)
            gq = max(0., bus.qg_min-st["qg"], st["qg"]-bus.qg_max)
            cost += bus.cost_quad*st["pg"]**2 + bus.cost_lin*st["pg"] + bus.cost_const
        else:
            gp, gq = abs(st["pg"]), abs(st["qg"])
        vviol = max(0., bus.v_min_pu-st["v"], st["v"]-bus.v_max_pu)
        if bus.is_slack and bus.fix_slack_voltage and bus.v_min_pu <= 1. <= bus.v_max_pu and bus.v_max_pu-bus.v_min_pu >= 1e-6:
            vviol = max(vviol, abs(st["v"]-1.))
        av = abs(st["va"]) if bus.is_slack else (0. if topo.full_ac_model else max(0., abs(st["va"])-1.25*theta_max))
        rows.append({"bus": bid, "dP": st["pg"]-bus.demand_p-injections[bid][0],
                     "dQ": st["qg"]-bus.demand_q-injections[bid][1],
                     "voltage_violation": vviol, "pg_violation": gp, "qg_violation": gq,
                     "angle_violation": av})
    mx = lambda field: max((abs(r[field]) for r in rows), default=0.)
    metrics = {"max_dP": mx("dP"), "max_dQ": mx("dQ"),
               "max_voltage_violation": mx("voltage_violation"),
               "max_pg_violation": mx("pg_violation"), "max_qg_violation": mx("qg_violation"),
               "max_angle_violation": max(mx("angle_violation"), branch_angle_violation),
               "max_thermal_violation": max((b["violation"] for b in branches), default=0.)}
    feasible = (metrics["max_dP"] <= tol.balance and metrics["max_dQ"] <= tol.balance
                and metrics["max_voltage_violation"] <= tol.voltage
                and metrics["max_pg_violation"] <= tol.generation
                and metrics["max_qg_violation"] <= tol.generation
                and metrics["max_thermal_violation"] <= tol.thermal
                and metrics["max_angle_violation"] <= tol.angle)
    return {"valid_data": True, "feasible": feasible, "errors": [], "generation_cost": cost,
            "tolerances": asdict(tol), **metrics, "bus_details": rows, "branches": branches}
