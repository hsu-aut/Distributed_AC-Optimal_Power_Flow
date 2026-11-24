"""
Live-Viz API für Distributed OPF.

Start (aus Repo-Root):
  python -m uvicorn viz.api:app --reload --port 8000

Dann Browser: http://localhost:8000/

Endpunkte:
  GET /api/topology?topology=topology_ieee118_full.yaml
  GET /api/layout?topology=...
  GET /api/state
  GET /api/stream   (SSE)
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAYOUTS = ROOT / "layouts"
STATIC = pathlib.Path(__file__).resolve().parent / "static"
DEFAULT_SNAPSHOT = pathlib.Path(os.getenv("LIVE_VIZ_SNAPSHOT", str(ROOT / "logs" / "live_snapshot.json")))

app = FastAPI(title="Distributed OPF Live Viz", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("LIVE_VIZ_CORS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_topology_path(name: Optional[str]) -> pathlib.Path:
    if not name:
        # Snapshot bevorzugen
        snap = _read_snapshot()
        if snap and snap.get("topology"):
            name = snap["topology"]
        else:
            name = os.getenv("LIVE_VIZ_TOPOLOGY", "topology_ieee118_full.yaml")
    path = pathlib.Path(name)
    if not path.is_file():
        path = ROOT / pathlib.Path(name).name
    if not path.is_file():
        raise HTTPException(404, f"Topology not found: {name}")
    return path


def _layout_path_for_topology(topo_path: pathlib.Path) -> pathlib.Path:
    name = topo_path.name.lower()
    if "ieee118" in name:
        return LAYOUTS / "ieee118_coords.json"
    if "3bus" in name:
        return LAYOUTS / "3bus_coords.json"
    stem = topo_path.stem.replace("topology_", "")
    return LAYOUTS / f"{stem}_coords.json"


def _read_json(path: pathlib.Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_snapshot() -> Optional[Dict[str, Any]]:
    path = DEFAULT_SNAPSHOT
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except (json.JSONDecodeError, OSError):
        return None


@app.get("/api/health")
def health() -> Dict[str, Any]:
    snap = _read_snapshot()
    return {
        "ok": True,
        "snapshot_exists": snap is not None,
        "iteration": None if snap is None else snap.get("iteration"),
        "topology": None if snap is None else snap.get("topology"),
    }


@app.get("/api/topology")
def get_topology(topology: Optional[str] = Query(None)) -> Dict[str, Any]:
    path = _resolve_topology_path(topology)
    try:
        import yaml
    except ImportError as exc:
        raise HTTPException(500, "PyYAML required") from exc
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    buses = []
    for b in raw.get("buses", []):
        buses.append(
            {
                "id": int(b["bus_id"]),
                "is_generator": bool(b.get("is_generator", False)),
                "is_slack": bool(b.get("is_slack", False)),
                "demand_p": float(b.get("demand_p", 0.0)),
                "demand_q": float(b.get("demand_q", 0.0)),
                "v_min_pu": float(b.get("v_min_pu", 0.9)),
                "v_max_pu": float(b.get("v_max_pu", 1.1)),
            }
        )
    lines = []
    for ln in raw.get("lines", []):
        lines.append(
            {
                "from": int(ln["from"]),
                "to": int(ln["to"]),
                "r_pu": float(ln.get("r_pu", 0.0)),
                "x_pu": float(ln.get("x_pu", 0.0)),
                "smax_pu": float(ln["smax_pu"]) if ln.get("smax_pu") is not None else None,
                "pmax_pu": float(ln.get("pmax_pu", 10.0)),
            }
        )
    return {
        "name": path.name,
        "base_mva": float(raw.get("base_mva", 100.0)),
        "buses": buses,
        "lines": lines,
    }


@app.get("/api/layout")
def get_layout(topology: Optional[str] = Query(None)) -> Dict[str, Any]:
    topo_path = _resolve_topology_path(topology)
    layout_path = _layout_path_for_topology(topo_path)
    if not layout_path.is_file():
        raise HTTPException(
            404,
            f"Layout missing: {layout_path.name}. "
            f"Run: python tools/generate_network_layout.py --topology {topo_path.name}",
        )
    data = _read_json(layout_path)
    data["layout_file"] = layout_path.name
    return data


@app.get("/api/state")
def get_state() -> JSONResponse:
    snap = _read_snapshot()
    if snap is None:
        return JSONResponse(
            {
                "iteration": 0,
                "mode": None,
                "topology": None,
                "converged": False,
                "buses": {},
                "edges": [],
                "residuals": {"prim_max": 0.0, "dual_max": 0.0},
                "waiting": True,
            }
        )
    snap = dict(snap)
    snap["waiting"] = False
    return JSONResponse(snap)


@app.get("/api/stream")
async def stream_state(interval_ms: int = Query(400, ge=100, le=5000)):
    async def event_gen():
        last_iter = None
        last_mtime = None
        while True:
            path = DEFAULT_SNAPSHOT
            mtime = path.stat().st_mtime if path.is_file() else None
            snap = _read_snapshot()
            it = None if snap is None else snap.get("iteration")
            if snap is not None and (it != last_iter or mtime != last_mtime):
                last_iter = it
                last_mtime = mtime
                yield {"event": "state", "data": json.dumps(snap)}
            else:
                yield {"event": "ping", "data": json.dumps({"t": time.time()})}
            await asyncio.sleep(interval_ms / 1000.0)

    return EventSourceResponse(event_gen())


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC / "index.html"
    if not index_path.is_file():
        raise HTTPException(404, "viz/static/index.html missing")
    return FileResponse(index_path)


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
