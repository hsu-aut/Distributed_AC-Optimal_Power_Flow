"""Topology data (buses, branches, limits, costs) from YAML and derived branch admittances."""
import pathlib
import cmath
import math
from dataclasses import dataclass
from typing import List, Dict

try:
    import yaml
except ImportError:  # pragma: no cover - wird bei fehlendem PyYAML aktiv
    yaml = None


@dataclass
class BusConfig:
    bus_id: int
    neighbors: List[int]
    is_generator: bool
    is_slack: bool
    pg_min: float
    pg_max: float
    cost_quad: float
    cost_lin: float
    demand_p: float
    demand_q: float = 0.0
    v_min_pu: float = 0.9
    v_max_pu: float = 1.1
    qg_min: float = 0.0
    qg_max: float = 0.0
    shunt_g_pu: float = 0.0
    shunt_b_pu: float = 0.0
    cost_const: float = 0.0
    fix_slack_voltage: bool = True


@dataclass
class LineConfig:
    from_bus: int
    to_bus: int
    x_pu: float
    b_cap: float
    r_pu: float = 0.0
    b_shunt_pu: float = 0.0
    pmax_pu: float = 10.0
    smax_pu: float | None = None
    tap_ratio: float = 1.0
    shift_rad: float = 0.0
    angle_min_rad: float | None = None
    angle_max_rad: float | None = None
    unlimited: bool = False

    @property
    def thermal_limit_pu(self) -> float:
        """Scheinleistungsgrenze; Fallback auf pmax_pu."""
        if self.unlimited:
            return math.inf
        if self.smax_pu is not None and self.smax_pu > 0:
            return float(self.smax_pu)
        return float(self.pmax_pu)


@dataclass
class TopologyConfig:
    base_mva: float
    buses: Dict[int, BusConfig]
    lines: List[LineConfig]
    full_ac_model: bool = False


def terminal_admittance(line: LineConfig, from_bus: int) -> tuple[complex, complex]:
    """Self/mutual admittances of the selected terminal, MATPOWER pi model."""
    y = 1 / complex(line.r_pu, line.x_pu)
    tap = line.tap_ratio * cmath.exp(1j * line.shift_rad)
    if line.tap_ratio <= 0:
        raise ValueError("Transformer tap must be positive")
    ytt = y + 0.5j * line.b_shunt_pu
    if from_bus == line.from_bus:
        return ytt / abs(tap)**2, -y / tap.conjugate()
    if from_bus == line.to_bus:
        return ytt, -y / tap
    raise ValueError("Bus is not a branch terminal")


def line_admittance(r_pu: float, x_pu: float) -> tuple[float, float]:
    """Serie-Leitwert g + j b aus r, x (p.u.)."""
    denom = r_pu * r_pu + x_pu * x_pu
    if denom <= 0.0:
        raise ValueError(f"Ungültige Leitungsparameter r={r_pu}, x={x_pu}")
    g = r_pu / denom
    b = -x_pu / denom
    return g, b


def _load_builtin_topology() -> TopologyConfig:
    """Fallback für Tests, falls keine YAML-Datei verfügbar ist."""
    buses: Dict[int, BusConfig] = {}
    for bus_id in range(5):
        neighbors = []
        if bus_id > 0:
            neighbors.append(bus_id - 1)
        if bus_id < 4:
            neighbors.append(bus_id + 1)
        buses[bus_id] = BusConfig(
            bus_id=bus_id,
            neighbors=neighbors,
            is_generator=bus_id == 0,
            is_slack=bus_id == 0,
            pg_min=0.0,
            pg_max=100.0 if bus_id == 0 else 0.0,
            cost_quad=0.01,
            cost_lin=10.0,
            demand_p=50.0 if bus_id > 0 else 0.0,
            demand_q=10.0 if bus_id > 0 else 0.0,
            qg_min=-50.0 if bus_id == 0 else 0.0,
            qg_max=50.0 if bus_id == 0 else 0.0,
        )
    lines: List[LineConfig] = []
    for i in range(4):
        lines.append(
            LineConfig(
                from_bus=i,
                to_bus=i + 1,
                x_pu=0.1,
                r_pu=0.01,
                b_cap=1e9,
                pmax_pu=10.0,
                smax_pu=10.0,
            )
        )
    return TopologyConfig(base_mva=100.0, buses=buses, lines=lines)


def load_topology_config(path: str | pathlib.Path | None) -> TopologyConfig:
    """
    Lade eine Topologie aus YAML.

    Erwartete Struktur (siehe tools/generate_topology_from_pandapower.py):
    base_mva: <float>
    buses:
      - bus_id: 0
        neighbors: [1, 2, ...]
        ...
    lines:
      - from: 0
        to: 1
        x_pu: 0.1
        ...
    """
    if path is None:
        return _load_builtin_topology()
    topo_path = pathlib.Path(path)
    if not topo_path.exists():
        raise FileNotFoundError(f"Topologie-Datei nicht gefunden: {topo_path}")
    if yaml is None:
        raise RuntimeError(
            "PyYAML ist nicht installiert. Bitte 'pip install pyyaml' ausführen "
            "oder requirements.txt installieren."
        )
    with topo_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Ungültige Topologie-Struktur in {topo_path}")

    base_mva = float(data.get("base_mva", 100.0))
    buses_raw = data.get("buses", [])
    if not buses_raw:
        raise ValueError(f"Topologie {topo_path} enthält keine 'buses'-Einträge")

    buses: Dict[int, BusConfig] = {}
    for entry in buses_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Ungültiger Bus-Eintrag: {entry}")
        bus_id = int(entry["bus_id"])
        if "neighbors" not in entry:
            raise ValueError(f"Bus {bus_id}: Feld 'neighbors' fehlt in Topologie")
        neighbors = [int(n) for n in entry.get("neighbors", [])]
        for req in ("demand_p", "pg_min", "pg_max", "cost_quad", "cost_lin", "is_generator", "is_slack"):
            if req not in entry:
                raise ValueError(f"Bus {bus_id}: Pflichtfeld '{req}' fehlt in Topologie")
        # YAML-Leistungen in MW / MVar → p.u.
        scale = (1.0 / base_mva) if base_mva else 1.0
        pg_min_pu = float(entry.get("pg_min", 0.0)) * scale
        pg_max_pu = float(entry.get("pg_max", 0.0)) * scale
        demand_p_pu = float(entry.get("demand_p", 0.0)) * scale
        demand_q_pu = float(entry.get("demand_q", 0.0)) * scale
        qg_min_pu = float(entry.get("qg_min", 0.0)) * scale
        qg_max_pu = float(entry.get("qg_max", 0.0)) * scale
        buses[bus_id] = BusConfig(
            bus_id=bus_id,
            neighbors=neighbors,
            is_generator=bool(entry.get("is_generator", False)),
            is_slack=bool(entry.get("is_slack", False)),
            pg_min=pg_min_pu,
            pg_max=pg_max_pu,
            cost_quad=float(entry.get("cost_quad", 0.0)) * (base_mva**2 if data.get("cost_power_unit") == "MW" else 1),
            cost_lin=float(entry.get("cost_lin", 0.0)) * (base_mva if data.get("cost_power_unit") == "MW" else 1),
            demand_p=demand_p_pu,
            demand_q=demand_q_pu,
            v_min_pu=float(entry.get("v_min_pu", 0.9)),
            v_max_pu=float(entry.get("v_max_pu", 1.1)),
            qg_min=qg_min_pu,
            qg_max=qg_max_pu,
            shunt_g_pu=float(entry.get("shunt_g_mw", 0.0)) * scale,
            shunt_b_pu=float(entry.get("shunt_b_mvar", 0.0)) * scale,
            cost_const=float(entry.get("cost_const", 0.0)),
            fix_slack_voltage=bool(entry.get("fix_slack_voltage", True)),
        )

    lines_raw = data.get("lines", [])
    lines: List[LineConfig] = []
    for entry in lines_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Ungültiger Linien-Eintrag: {entry}")
        from_bus = entry.get("from")
        to_bus = entry.get("to")
        if from_bus is None or to_bus is None:
            from_bus = entry.get("from_bus")
            to_bus = entry.get("to_bus")
        if from_bus is None or to_bus is None:
            raise ValueError(f"Linien-Eintrag ohne 'from'/'to': {entry}")
        raw_pmax = entry.get("pmax_pu", None)
        if raw_pmax is None:
            b_cap = entry.get("b_cap", None)
            if b_cap is None:
                raise ValueError(
                    f"Linie {from_bus}->{to_bus}: weder pmax_pu noch b_cap angegeben; Topologie unvollständig"
                )
            pmax_pu_val = float(b_cap) / base_mva if base_mva else float(b_cap)
        else:
            pmax_pu_val = float(raw_pmax)

        raw_smax = entry.get("smax_pu", None)
        if raw_smax is None:
            raw_smax_mw = entry.get("smax_mva", entry.get("s_cap", None))
            if raw_smax_mw is not None:
                smax_pu_val = float(raw_smax_mw) / base_mva if base_mva else float(raw_smax_mw)
            else:
                smax_pu_val = pmax_pu_val
        else:
            smax_pu_val = float(raw_smax)

        if "x_pu" not in entry:
            raise ValueError(f"Linie {from_bus}->{to_bus}: Pflichtfeld 'x_pu' fehlt")
        lines.append(
            LineConfig(
                from_bus=int(from_bus),
                to_bus=int(to_bus),
                x_pu=float(entry["x_pu"]),
                b_cap=float(entry.get("b_cap", 0.0)),
                r_pu=float(entry.get("r_pu", 0.0)),
                b_shunt_pu=float(entry.get("b_shunt_pu", 0.0)),
                pmax_pu=pmax_pu_val,
                smax_pu=smax_pu_val,
                tap_ratio=float(entry.get("tap_ratio", 1.0)),
                shift_rad=math.radians(float(entry.get("shift_degree", 0.0))),
                angle_min_rad=math.radians(float(entry["angle_min_degree"])) if entry.get("angle_min_degree") is not None else None,
                angle_max_rad=math.radians(float(entry["angle_max_degree"])) if entry.get("angle_max_degree") is not None else None,
                unlimited=bool(entry.get("unlimited", False)),
            )
        )

    return TopologyConfig(base_mva=base_mva, buses=buses, lines=lines, full_ac_model=bool(data.get("full_ac_model", False)))
