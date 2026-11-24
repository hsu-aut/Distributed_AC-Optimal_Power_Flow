# Live-Visualisierung (lokal)

Zeigt das Netz (z. B. IEEE 118) während ADMM-AC läuft. Später dieselbe API in React einbinden.

## Ablauf lokal

**Terminal 1 – API + Viewer**

```bash
conda activate distributed_opf
pip install fastapi uvicorn sse-starlette
python -m uvicorn viz.api:app --reload --port 8000
```

Browser: http://localhost:8000/

**Terminal 2 – ADMM AC (z. B. 3-Bus)**

```bash
conda activate distributed_opf
# Snapshot ist standardmäßig an (LIVE_VIZ=1)
python tools/run_ac_local.py --topology topology_3bus.yaml
```

IEEE 118 analog:

```bash
python tools/run_ac_local.py --topology topology_ieee118_full.yaml
```

Der Rank-0-Prozess schreibt `logs/live_snapshot.json` nach jeder Iteration. Der Viewer aktualisiert sich per SSE.

## Endpunkte (für React)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/api/topology?topology=…` | Busse + Leitungen |
| GET | `/api/layout?topology=…` | Feste x/y-Koordinaten |
| GET | `/api/state` | Aktueller Snapshot |
| GET | `/api/stream` | SSE-Stream (`event: state`) |
| GET | `/api/health` | Status |

CORS ist standardmäßig `*` (`LIVE_VIZ_CORS` zum Einschränken).

## Layouts

- `layouts/ieee118_coords.json` – aus pandapower `case118().bus_geodata`
- `layouts/3bus_coords.json` – Schema-Dreieck

Neu erzeugen:

```bash
python tools/generate_network_layout.py --topology topology_ieee118_full.yaml
```

## Umgebungsvariablen

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `LIVE_VIZ` | `1` | Snapshot an/aus |
| `LIVE_VIZ_EVERY` | `1` | nur jede N-te Iteration schreiben |
| `LIVE_VIZ_SNAPSHOT` | `logs/live_snapshot.json` | Snapshot-Pfad |
| `LIVE_VIZ_TOPOLOGY` | `topology_ieee118_full.yaml` | Fallback wenn noch kein Snapshot |

## Pi-Cluster (später)

Gleicher Code: Snapshot auf dem Master (Rank 0), API auf dem Master-Pi, React/Browser im LAN gegen `http://<master>:8000`.
