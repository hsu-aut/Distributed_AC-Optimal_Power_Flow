"""Initial ADMM states for the study of initialization and local optima.

Standard is the flat start (V=1, theta=0, Pg=Qg=0, z from that state, u=0). A reference
start takes V, theta, Pg, Qg of a solved central run. An optional deterministic
perturbation of scale sigma (seeded) is applied to a reference start:

    V     <- clip(V_ref * (1 + sigma * xi_V), v_min, v_max)
    theta <- theta_ref + sigma * xi_theta          (reference bus stays 0)
    Pg    <- clip(Pg_ref * (1 + sigma * xi_P), pg_min, pg_max)   generators only
    Qg    <- clip(Qg_ref * (1 + sigma * xi_Q), qg_min, qg_max)   generators only

with independent standard normal xi per bus. The perturbation only changes where ADMM
starts; model, penalties, tolerances and acceptance stay unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

import numpy as np


def flat_reference(topology) -> Dict[int, Dict[str, float]]:
    """Flat point V=1 (clipped to the band), theta=0, Pg=Qg=0 as a reference for perturbations."""
    return {b: {"v": min(bus.v_max_pu, max(bus.v_min_pu, 1.0)), "va": 0.0, "pg": 0.0, "qg": 0.0}
            for b, bus in topology.buses.items()}


def load_central_reference(path: Path) -> Dict[int, Dict[str, float]]:
    """V, theta, Pg, Qg per bus from a central run directory, its result.json, or a PYPOWER reference.json."""
    path = Path(path)
    result_path = path / "result.json" if path.is_dir() else path
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if "states" in result:
        if not result.get("success", False):
            raise ValueError(f"Reference {result_path} is not an accepted solution")
        return {int(b): {k: float(s[k]) for k in ("v", "va", "pg", "qg")} for b, s in result["states"].items()}
    central = result.get("central", result)
    if not result.get("success", True):
        raise ValueError(f"Reference {result_path} is not an accepted central run")
    return {int(b): {"v": float(central["v"][b]), "va": float(central["theta"][b]),
                     "pg": float(central["pg"][b]), "qg": float(central["qg"][b])} for b in central["v"]}


def build_initial_state(topology, reference: Dict[int, Dict[str, float]], perturbation: float = 0.0,
                        seed: int = 0) -> Dict[int, Dict[str, float]]:
    if set(reference) != set(topology.buses):
        raise ValueError("Reference state must cover exactly the topology buses")
    if perturbation < 0:
        raise ValueError("perturbation must be non-negative")
    rng = np.random.default_rng(seed)
    state = {}
    for bus_id in sorted(topology.buses):
        bus, ref = topology.buses[bus_id], reference[bus_id]
        xi_v, xi_t, xi_p, xi_q = rng.standard_normal(4)
        s = float(perturbation)
        v = min(bus.v_max_pu, max(bus.v_min_pu, ref["v"] * (1.0 + s * xi_v)))
        va = 0.0 if bus.is_slack else ref["va"] + s * xi_t
        if bus.is_generator:
            pg = min(bus.pg_max, max(bus.pg_min, ref["pg"] * (1.0 + s * xi_p)))
            qg = min(bus.qg_max, max(bus.qg_min, ref["qg"] * (1.0 + s * xi_q)))
        else:
            pg = qg = 0.0
        state[bus_id] = {"v": float(v), "va": float(va), "pg": float(pg), "qg": float(qg)}
    return state


def write_initial_state(path: Path, state: Dict[int, Dict[str, float]]) -> str:
    text = json.dumps({str(b): s for b, s in sorted(state.items())}, indent=1)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_initial_state(path: Path) -> Dict[int, Dict[str, float]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(b): {k: float(s[k]) for k in ("v", "va", "pg", "qg")} for b, s in data.items()}
