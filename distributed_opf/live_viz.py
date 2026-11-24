"""Live-Visualisierung: aggregierter Snapshot nach jeder ADMM-Iteration.

Rank 0 schreibt atomar ``logs/live_snapshot.json``. Die FastAPI unter ``viz/``
liest diese Datei; MPI-Agenten rufen ``publish_live_snapshot`` nach dem Loggen auf.

Steuerung per Umgebung:
  LIVE_VIZ=0          Snapshot abschalten (Default: an)
  LIVE_VIZ_EVERY=N    nur jede N-te Iteration schreiben (Gather läuft weiter)
  LIVE_VIZ_SNAPSHOT   Zielpfad (Default: logs/live_snapshot.json)
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Dict, Iterable, Mapping, Optional


def live_viz_enabled() -> bool:
    return os.getenv("LIVE_VIZ", "1").strip().lower() not in ("0", "false", "no", "off")


def snapshot_path() -> pathlib.Path:
    return pathlib.Path(os.getenv("LIVE_VIZ_SNAPSHOT", "logs/live_snapshot.json"))


def build_bus_record_ac(bus_id: int, state: Any) -> Dict[str, Any]:
    """Kompaktes Bus-Dict aus AcLocalState."""
    return {
        "bus_id": int(bus_id),
        "v": float(getattr(state, "v", 1.0)),
        "va": float(getattr(state, "va", 0.0)),
        "pg": float(getattr(state, "pg", 0.0)),
        "qg": float(getattr(state, "qg", 0.0)),
        "pij": {str(k): float(v) for k, v in (getattr(state, "pij", None) or {}).items()},
        "qij": {str(k): float(v) for k, v in (getattr(state, "qij", None) or {}).items()},
        "prim_res": float(getattr(state, "prim_res", 0.0)),
        "dual_res": float(getattr(state, "dual_res", 0.0)),
        "generation_cost": float(getattr(state, "generation_cost", 0.0)),
        "p_slack": float(getattr(state, "p_slack", 0.0)),
        "q_slack": float(getattr(state, "q_slack", 0.0)),
    }


def build_local_buses_ac(bus_ids: Iterable[int], states: Mapping[int, Any]) -> Dict[int, Dict[str, Any]]:
    return {int(bid): build_bus_record_ac(int(bid), states[bid]) for bid in bus_ids}


def _edges_from_buses(buses: Mapping[str, Mapping[str, Any]]) -> list[Dict[str, Any]]:
    """Eine Kante pro Leitung (from < to), Fluss aus Sicht des from-Busses."""
    edges: list[Dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for bid_s, data in buses.items():
        i = int(bid_s)
        pij = data.get("pij") or {}
        qij = data.get("qij") or {}
        for j_s, pval in pij.items():
            j = int(j_s)
            a, b = (i, j) if i < j else (j, i)
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            # Fluss a→b: wenn i==a, pval ist a→b; sonst Negation von b→a
            if i == a:
                p_ab = float(pval)
                q_ab = float(qij.get(str(j), qij.get(j, 0.0)))
            else:
                p_ab = -float(pval)
                q_ab = -float(qij.get(str(j), qij.get(j, 0.0)))
            edges.append(
                {
                    "from": a,
                    "to": b,
                    "pij": p_ab,
                    "qij": q_ab,
                    "s": (p_ab * p_ab + q_ab * q_ab) ** 0.5,
                }
            )
    return edges


def _atomic_write_json(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, separators=(",", ":"))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def publish_live_snapshot(
    world: Any,
    rank: int,
    local_buses: Dict[int, Dict[str, Any]],
    *,
    iteration: int,
    mode: str = "ac",
    topology: Optional[str] = None,
    converged: bool = False,
) -> None:
    """Gather aller Bus-States → Rank 0 schreibt live_snapshot.json."""
    if not live_viz_enabled():
        return

    # Alle Ranks müssen am Gather teilnehmen
    gathered = world.gather(local_buses, root=0)
    if rank != 0:
        return

    every = max(1, int(os.getenv("LIVE_VIZ_EVERY", "1")))
    if iteration > 0 and (iteration % every) != 0 and not converged:
        return

    buses: Dict[str, Any] = {}
    for part in gathered or []:
        if not part:
            continue
        for bid, data in part.items():
            buses[str(int(bid))] = data

    prim_vals = [float(b.get("prim_res", 0.0)) for b in buses.values()]
    dual_vals = [float(b.get("dual_res", 0.0)) for b in buses.values()]
    snapshot = {
        "iteration": int(iteration),
        "mode": mode,
        "topology": topology,
        "converged": bool(converged),
        "timestamp": time.time(),
        "n_buses": len(buses),
        "buses": buses,
        "edges": _edges_from_buses(buses),
        "residuals": {
            "prim_max": max(prim_vals) if prim_vals else 0.0,
            "dual_max": max(dual_vals) if dual_vals else 0.0,
        },
    }
    _atomic_write_json(snapshot_path(), snapshot)
