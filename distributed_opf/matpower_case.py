"""Read numeric MATPOWER case tables without executing MATLAB code."""
import hashlib
from pathlib import Path
import re
import numpy as np


def read_case(path):
    text = Path(path).read_text(encoding="utf-8")
    clean = re.sub(r"%[^\n]*", "", text)
    base = re.search(r"mpc\.baseMVA\s*=\s*([\d.eE+-]+)\s*;", clean)
    if not base:
        raise ValueError("Missing baseMVA")
    case = {"version": "2", "baseMVA": float(base[1])}
    for name in ("bus", "gen", "branch", "gencost"):
        match = re.search(r"mpc\." + name + r"\s*=\s*\[(.*?)\]\s*;", clean, re.S)
        if not match:
            raise ValueError(f"Missing numeric table {name}")
        case[name] = np.array([[float(x) for x in row.split()] for row in match[1].split(";") if row.strip()])
    return case


def export_topology(path):
    case = read_case(path)
    base = case["baseMVA"]
    ids = {int(row[0]): i for i, row in enumerate(case["bus"])}
    if len(ids) != len(case["bus"]) or sum(case["bus"][:, 1] == 3) != 1:
        raise ValueError("Require unique buses and one angle reference")
    gens = {}
    if len(case["gencost"]) != len(case["gen"]):
        raise ValueError("Reactive generation costs require explicit support")
    for gen, cost in zip(case["gen"], case["gencost"]):
        if gen[7] <= 0:
            continue
        bus = int(gen[0])
        if bus in gens:
            raise ValueError("Multiple generators at a bus require individual generator variables; no aggregation allowed")
        if cost[0] != 2 or cost[3] != 3 or np.any(gen[10:16] != 0):
            raise ValueError("Unsupported cost or capability curve; refusing a simplified export")
        gens[bus] = (gen, cost)
    buses = []
    for row in case["bus"]:
        if row[1] == 4:
            raise ValueError("Isolated buses require explicit island handling")
        g, c = gens.get(int(row[0]), (np.zeros(21), np.zeros(7)))
        buses.append(dict(bus_id=ids[int(row[0])], original_bus_id=int(row[0]), neighbors=[],
            is_generator=int(row[0]) in gens, is_slack=row[1] == 3,
            pg_min=float(g[9]), pg_max=float(g[8]), qg_min=float(g[4]), qg_max=float(g[3]),
            demand_p=float(row[2]), demand_q=float(row[3]), shunt_g_mw=float(row[4]),
            shunt_b_mvar=float(row[5]), v_min_pu=float(row[12]), v_max_pu=float(row[11]),
            cost_quad=float(c[4]), cost_lin=float(c[5]), cost_const=float(c[6]), fix_slack_voltage=False))
    lines = []
    for index, row in enumerate(case["branch"]):
        if row[10] <= 0:
            continue
        f, t = ids[int(row[0])], ids[int(row[1])]
        lines.append(dict(original_branch_id=index, **{"from": f, "to": t},
            r_pu=float(row[2]), x_pu=float(row[3]), b_shunt_pu=float(row[4]),
            smax_mva=float(row[5]), pmax_pu=float(row[5]/base), unlimited=row[5] == 0,
            tap_ratio=float(row[8]) if row[8] else 1., shift_degree=float(row[9]),
            angle_min_degree=float(row[11]) if row[11] > -360 else None,
            angle_max_degree=float(row[12]) if row[12] < 360 else None))
        buses[f]["neighbors"].append(t)
        buses[t]["neighbors"].append(f)
    for bus in buses:
        bus["neighbors"] = sorted(set(bus["neighbors"]))
        bus["is_slack"] = bool(bus["is_slack"])
    for line in lines:
        line["unlimited"] = bool(line["unlimited"])
    return dict(base_mva=base, full_ac_model=True, cost_power_unit="MW",
        source="MATPOWER 8.1 data/case118.m", source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        buses=buses, lines=lines)


def internal_case(path):
    """PYPOWER input, retaining source values except a uniform angle gauge shift."""
    case = read_case(path)
    ids = {int(row[0]): i for i, row in enumerate(case["bus"])}
    for row in case["gen"]:
        row[0] = ids[int(row[0])]
    for row in case["branch"]:
        row[0], row[1] = ids[int(row[0])], ids[int(row[1])]
    case["bus"][:, 0] = np.arange(len(ids))
    case["bus"][:, 8] -= case["bus"][case["bus"][:, 1] == 3, 8][0]
    from pandapower.pypower.idx_brch import branch_cols
    case["branch"] = np.pad(case["branch"], ((0, 0), (0, branch_cols-case["branch"].shape[1])))
    return case
