#!/usr/bin/env python3
"""
MPI-Entry-Point für den Pi-Cluster.

Dieses Skript wird von `mpirun` aufgerufen (lokal über tools/run_ac_local.py, auf dem
Cluster mit Hostfile, Orchestrator pi119) und startet genau einen ADMM-Agenten pro MPI-Rank.
"""

import argparse
import pathlib
import sys
from mpi4py import MPI

try:
    import yaml
except ImportError:
    yaml = None

from distributed_opf.config import load_topology_config


def _load_agent_class(mode: str):
    mode = (mode or "ac").strip().lower()
    if mode != "ac":
        raise SystemExit("Only --mode ac is supported; the DC-OPF implementation is kept under legacy/")
    from distributed_opf.agent_ac_mpi import AcAgent as Agent
    return Agent


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Starte AC-OPF-ADMM-Agenten (ein Agent pro MPI-Rank)"
    )
    parser.add_argument(
        "--topology",
        type=str,
        default="topology_3bus.yaml",
        help="Pfad zur Topologie-Datei (YAML)",
    )
    parser.add_argument(
        "--hostfile",
        type=str,
        default="",
        help="(Optional) Hostfile-Pfad – nur für Logging, wird nicht ausgewertet",
    )
    parser.add_argument(
        "--buses",
        type=int,
        nargs="+",
        default=None,
        help="Liste von Bus-IDs, die auf den Ranks laufen sollen (Standard: alle aus Topologie)",
    )
    parser.add_argument(
        "--partition-config",
        type=str,
        default=None,
        help="Partitionierungs-Config (YAML) für Multi-Bus-Modus",
    )
    parser.add_argument(
        "--mode",
        choices=("ac",),
        default=None,
        help="OPF-Modus (nur ac)",
    )
    return parser


def determine_bus_assignment(selected_buses, size, rank):
    if len(selected_buses) != size:
        if rank == 0:
            print(
                f"[Launcher] Fehler: Anzahl der MPI-Ranks ({size}) stimmt nicht mit der Anzahl der ausgewählten Busse "
                f"({len(selected_buses)}) überein.",
                flush=True,
            )
            print(
                "[Launcher] Bitte `mpirun -np` anpassen oder `--buses` korrekt setzen.",
                flush=True,
            )
        MPI.COMM_WORLD.Abort(1)

    bus_to_rank = {bus_id: idx for idx, bus_id in enumerate(selected_buses)}
    if rank not in bus_to_rank.values():
        # Sollte nie passieren, defensive Absicherung
        if rank == 0:
            print("[Launcher] Interner Fehler: Rank außerhalb des Bus-Mappings.", flush=True)
        MPI.COMM_WORLD.Abort(1)

    # Nutze Reihenfolge in selected_buses als Mapping Rank -> Bus
    bus_id = selected_buses[rank]
    return bus_id, bus_to_rank


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    import os
    opf_mode = (args.mode or os.environ.get("OPF_MODE", "ac")).strip().lower()
    os.environ["OPF_MODE"] = opf_mode
    Agent = _load_agent_class(opf_mode)

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank == 0:
        print(f"[Launcher] MPI-Setup: size={size}, topology={args.topology}, mode={opf_mode}", flush=True)
        if args.hostfile:
            print(f"[Launcher] Hostfile (nur Info): {args.hostfile}", flush=True)
        if args.buses:
            print(f"[Launcher] Vorgabe --buses: {args.buses}", flush=True)
        if args.partition_config:
            print(f"[Launcher] Partition-Config: {args.partition_config}", flush=True)

    # Lade Partition-Config, falls angegeben
    partition_config = None
    if args.partition_config:
        if yaml is None:
            if rank == 0:
                print("[ERROR] PyYAML wird für --partition-config benötigt. Bitte 'pip install pyyaml' ausführen.", flush=True)
            comm.Abort(1)
            return 1
        
        partition_path = pathlib.Path(args.partition_config)
        if not partition_path.exists():
            if rank == 0:
                print(f"[ERROR] Partition-Config nicht gefunden: {partition_path}", flush=True)
            comm.Abort(1)
            return 1
        
        try:
            with partition_path.open("r", encoding="utf-8") as fh:
                partition_config = yaml.safe_load(fh)
            if not isinstance(partition_config, dict):
                if rank == 0:
                    print(f"[ERROR] Ungültige Partition-Config-Struktur in {partition_path}", flush=True)
                comm.Abort(1)
                return 1
        except Exception as exc:
            if rank == 0:
                print(f"[ERROR] Fehler beim Laden der Partition-Config: {exc}", flush=True)
            comm.Abort(1)
            return 1

    try:
        topology = load_topology_config(args.topology)
    except Exception as exc:
        if rank == 0:
            print(f"[Launcher] Fehler beim Laden der Topologie '{args.topology}': {exc}", flush=True)
        comm.Abort(1)
        return 1  # für Typchecker

    all_bus_ids = sorted(topology.buses.keys())

    # Multi-Bus-Modus: Lade Partition-Config
    if partition_config:
        agents_cfg = partition_config.get("agents")
        if not isinstance(agents_cfg, list) or not agents_cfg:
            if rank == 0:
                print("[ERROR] Partition-Config muss eine 'agents'-Liste enthalten.", flush=True)
            comm.Abort(1)
            return 1

        rank_to_bus_ids = {}
        rank_to_neighbor_ranks = {}
        bus_to_rank_map = {}
        for entry in agents_cfg:
            if not isinstance(entry, dict):
                if rank == 0:
                    print(f"[ERROR] Ungültiger Agent-Eintrag in Partition-Config: {entry}", flush=True)
                comm.Abort(1)
                return 1
            if "rank" not in entry or "bus_ids" not in entry:
                if rank == 0:
                    print(f"[ERROR] Agent-Eintrag erfordert 'rank' und 'bus_ids': {entry}", flush=True)
                comm.Abort(1)
                return 1
            entry_rank = int(entry["rank"])
            bus_ids = [int(b) for b in entry.get("bus_ids", [])]
            if not bus_ids:
                if rank == 0:
                    print(f"[ERROR] Agent {entry_rank} hat keine Busse zugewiesen.", flush=True)
                comm.Abort(1)
                return 1
            
            rank_to_bus_ids[entry_rank] = bus_ids
            for bus_id in bus_ids:
                bus_to_rank_map[bus_id] = entry_rank

            neighbor_ranks = entry.get("neighbor_ranks")
            if neighbor_ranks is not None:
                rank_to_neighbor_ranks[entry_rank] = [int(n) for n in neighbor_ranks]

        num_ranks_from_config = len(rank_to_bus_ids)
        
        # Prüfe ob Anzahl der MPI-Ranks mit Config übereinstimmt
        if size != num_ranks_from_config:
            if rank == 0:
                print(
                    f"[ERROR] Anzahl der MPI-Ranks ({size}) stimmt nicht mit Partition-Config ({num_ranks_from_config} Agents) überein.",
                    flush=True,
                )
                print(
                    "[ERROR] Bitte `mpirun -np` anpassen oder Partition-Config korrigieren.",
                    flush=True,
                )
            comm.Abort(1)
            return 1

        # Prüfe ob dieser Rank in der Config vorhanden ist
        if rank not in rank_to_bus_ids:
            if rank == 0:
                print(f"[ERROR] Rank {rank} ist nicht in der Partition-Config definiert.", flush=True)
                print(f"[ERROR] Verfügbare Ranks: {sorted(rank_to_bus_ids.keys())}", flush=True)
            comm.Abort(1)
            return 1

        bus_ids = rank_to_bus_ids[rank]
        neighbor_ranks = rank_to_neighbor_ranks.get(rank, [])

        if rank == 0:
            print(f"[Launcher] Partition-Config geladen ({num_ranks_from_config} Agents)", flush=True)
            for r, buses in sorted(rank_to_bus_ids.items()):
                print(f"  Rank {r}: Buses {buses}, Neighbors: {rank_to_neighbor_ranks.get(r, [])}", flush=True)

        print(
            f"[Rank {rank}] Starte Multi-Bus-Agent für Buses {bus_ids} mit Neighbor Ranks: {neighbor_ranks}",
            flush=True,
        )

        try:
            agent = Agent(
                bus_ids=bus_ids,
                neighbor_ranks=neighbor_ranks,
                topology_path=args.topology,
                bus_to_rank_map=bus_to_rank_map,
            )
            agent.run()
        except Exception as exc:
            print(f"[Rank {rank}] FEHLER: {exc}", file=sys.stderr, flush=True)
            raise

        print(f"[Rank {rank}] Multi-Bus-Agent für Buses {bus_ids} beendet.", flush=True)
        return 0

    # Single-Bus-Modus (Backward-Compat)
    if args.buses:
        selected_buses = []
        missing = []
        for value in args.buses:
            if value in topology.buses:
                selected_buses.append(int(value))
            else:
                missing.append(value)
        if missing and rank == 0:
            print(f"[Launcher] WARNUNG: Ungültige Bus-IDs (nicht in Topologie): {missing}", flush=True)
        if not selected_buses:
            if rank == 0:
                print("[Launcher] Keine gültigen Busse in --buses gefunden – breche ab.", flush=True)
            comm.Abort(1)
        selected_buses = sorted(selected_buses)
    else:
        selected_buses = all_bus_ids

    bus_id, bus_to_rank = determine_bus_assignment(selected_buses, size, rank)

    # Aktuell setzen wir voraus, dass Bus-IDs == MPI-Ranks (0..N-1) entsprechen,
    # da der Agent im Single-Bus-Modus diese Annahme trifft.
    # Prüfe diese Voraussetzung einmal zentral.
    if rank == 0:
        identity_mapping = all(
            bus_id_value == idx for idx, bus_id_value in enumerate(selected_buses)
        )
        if not identity_mapping:
            print(
                "[Launcher] Fehler: Bus-IDs stimmen nicht mit MPI-Ranks überein. "
                "Bitte Topologie vorab auf 0..N-1 reindizieren oder Multi-Bus-Launch erweitern.",
                flush=True,
            )
            MPI.COMM_WORLD.Abort(1)

    bus_cfg = topology.buses[bus_id]
    neighbor_bus_ids = [nbr for nbr in bus_cfg.neighbors if nbr in bus_to_rank]
    missing_neighbors = sorted(set(bus_cfg.neighbors) - set(neighbor_bus_ids))

    if missing_neighbors:
        print(
            f"[Rank {rank}] WARNUNG: Einige Topologie-Nachbarn von Bus {bus_id} "
            f"sind nicht Teil des Rankings: {missing_neighbors}",
            flush=True,
        )

    print(
        f"[Rank {rank}] Starte Agent für Bus {bus_id} mit Nachbarn (Bus-IDs): {neighbor_bus_ids}",
        flush=True,
    )

    try:
        agent = Agent(
            bus_id=bus_id,
            neighbor_ids=neighbor_bus_ids,
            topology_path=args.topology,
        )
        agent.run()
    except Exception as exc:  # pragma: no cover - Laufzeitumgebung
        print(f"[Rank {rank}] FEHLER: {exc}", file=sys.stderr, flush=True)
        raise

    print(f"[Rank {rank}] Agent für Bus {bus_id} beendet.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        # A failed rank must not enter MPI finalization while peers are still
        # waiting in collectives. Preserve the traceback before aborting all.
        import traceback
        traceback.print_exc()
        sys.stderr.flush()
        MPI.COMM_WORLD.Abort(1)
        raise
    sys.exit(exit_code)

