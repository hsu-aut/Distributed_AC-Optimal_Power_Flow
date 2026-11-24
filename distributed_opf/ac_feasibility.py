"""Global AC power-balance check from bus-wise V/theta/Pg/Qg (exact AC equations)."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Sequence

from distributed_opf.config import TopologyConfig, line_admittance


def compute_ac_balance(
    topo: TopologyConfig,
    V: Mapping[int, float],
    th: Mapping[int, float],
    Pg: Mapping[int, float],
    Qg: Mapping[int, float],
) -> Dict[str, Any]:
    """Return max/sum P/Q mismatch using exact pi-model branch flows."""
    from distributed_opf.ac_validation import validate_ac
    states = {b: dict(v=V[b], va=th[b], pg=Pg[b], qg=Qg[b]) for b in topo.buses}
    result = validate_ac(topo, states)
    if not result['valid_data']:
        raise ValueError(result['errors'])
    return dict(max_dP=result['max_dP'], max_dQ=result['max_dQ'],
                sum_abs_dP=sum(abs(r['dP']) for r in result['bus_details']),
                sum_abs_dQ=sum(abs(r['dQ']) for r in result['bus_details']))

def balance_from_agent_states(
    topo: TopologyConfig,
    states: Mapping[int, Any],
) -> Dict[str, Any]:
    def val(st, key):
        return float(st[key] if isinstance(st, Mapping) else getattr(st, key))
    V = {int(b): val(st, "v") for b, st in states.items()}
    th = {int(b): val(st, "va") for b, st in states.items()}
    Pg = {int(b): val(st, "pg") for b, st in states.items()}
    Qg = {int(b): val(st, "qg") for b, st in states.items()}
    return compute_ac_balance(topo, V, th, Pg, Qg)


def logged_pij_balance(
    topo: TopologyConfig,
    records: Mapping[int, Mapping[str, Any]],
) -> Dict[str, float]:
    """Local ADMM balance: Pg - Pd - sum(pij_out) using logged branch flows."""
    max_dp = 0.0
    max_dq = 0.0
    for bus_id, bus in topo.buses.items():
        rec = records.get(bus_id)
        if rec is None:
            continue
        pg = float(rec.get("pg", 0.0))
        qg = float(rec.get("qg", 0.0))
        pd = float(bus.demand_p)
        qd = float(bus.demand_q)
        pij = rec.get("pij") or {}
        qij = rec.get("qij") or {}
        p_sum = sum(float(v) for v in pij.values())
        q_sum = sum(float(v) for v in qij.values())
        max_dp = max(max_dp, abs(pg - pd - p_sum - bus.shunt_g_pu*float(rec["v"])**2))
        max_dq = max(max_dq, abs(qg - qd - q_sum + bus.shunt_b_pu*float(rec["v"])**2))
    return {"max_logged_dP": max_dp, "max_logged_dQ": max_dq}
