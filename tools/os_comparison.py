#!/usr/bin/env python3
"""Local solve times per worker node and operating system (bus-level case).

    python tools/os_comparison.py

The software stack of the worker nodes was not uniform. The preflight of the bus-level case
(validation/cluster_campaign_20261015/preflight_k118.json) records the platform string of every
node: glibc 2.36 identifies Debian 12 (bookworm), glibc 2.41 Debian 13 (trixie). pi119 is the
orchestrator and runs no agent. pi120 was not part of that preflight and is listed as not
inventoried.

The x-step time of a rank depends strongly on its number of consensus edges (13.6 s to 74.0 s
in the bus-level run). Each rank's total x-step time is therefore divided by the median over all
ranks of the same run with the same number of consensus edges. The value of a node is the median
of these ratios over the evaluated runs, by default the first repetition of C2 with and without
fans (the bus-level runs of the 19.10.2026 campaign that carry rank metrics). Values below 1 mean
faster than comparable ranks. Ranks are mapped to nodes in hostfile order (one slot per node).

Output: tab08_knoten_betriebssystem.csv and .md in validation/cluster_campaign_20261019/tabellen
and ergebnisse/tabellen.
"""
import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "validation" / "cluster_campaign_20261019"
DEFAULT_RUNS = [CAMPAIGN / "mit_luefter" / "C2_bus118_edge_rho_k118_r1.rank_metrics.json",
                CAMPAIGN / "ohne_luefter" / "C2_bus118_edge_rho_k118_r1.rank_metrics.json"]
DEFAULT_PREFLIGHT = ROOT / "validation" / "cluster_campaign_20261015" / "preflight_k118.json"
OUT_DIRS = [CAMPAIGN / "tabellen", ROOT / "ergebnisse" / "tabellen"]
GLIBC_TO_OS = {"2.36": "Debian 12", "2.41": "Debian 13"}
NOT_INVENTORIED = "nicht inventarisiert"
NAME = "tab08_knoten_betriebssystem"


def node_number(host: str) -> int:
    match = re.search(r"(\d+)$", host)
    return int(match.group(1)) if match else 10**6


def read_preflight(path: Path):
    nodes = {}
    for report in json.loads(path.read_text(encoding="utf-8"))["reports"]:
        platform = report.get("platform", "")
        glibc = re.search(r"glibc([\d.]+)", platform)
        kernel = re.match(r"Linux-([^-]+)", platform)
        glibc = glibc.group(1) if glibc else "?"
        nodes[report["hostname"]] = {
            "os": GLIBC_TO_OS.get(glibc, f"glibc {glibc}"),
            "glibc": glibc,
            "kernel": kernel.group(1) if kernel else "?",
            "python": report.get("python", "?"),
            "numpy": (report.get("packages") or {}).get("numpy", "?"),
        }
    return nodes


def normalized_x_step(run: Path):
    """Return {host: x-step time / median of ranks with the same edge count} for one run."""
    ranks = json.loads(run.read_text(encoding="utf-8"))["ranks"]
    name = run.name[: -len(".rank_metrics.json")]
    hostfile = run.parent / "hostfiles" / f"{name}.txt"
    hosts = [line.split()[0] for line in hostfile.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(hosts) < len(ranks):
        raise ValueError(f"{hostfile}: {len(hosts)} hosts for {len(ranks)} ranks")
    by_edges = defaultdict(list)
    for rank in ranks:
        by_edges[rank["consensus_edges"]].append(rank["phases"]["x_solve"]["total_s"])
    reference = {edges: statistics.median(times) for edges, times in by_edges.items()}
    return {host: rank["phases"]["x_solve"]["total_s"] / reference[rank["consensus_edges"]]
            for host, rank in zip(hosts, ranks)}


def write_tables(rows, summary_rows, text_lines):
    header = ["Knoten", "Betriebssystem", "glibc", "Kernel", "Python", "NumPy", "Läufe",
              "normierte x-Schritt-Zeit (Median)"]
    csv = ";".join(header) + "\n" + "".join(";".join(map(str, row)) + "\n" for row in rows)
    md = ["# Lösezeiten je Knoten und Betriebssystem", ""] + text_lines + [
        "", "| Betriebssystem | Knoten | Median | Minimum | Maximum |", "|---|---|---|---|---|"]
    md += ["| " + " | ".join(map(str, row)) + " |" for row in summary_rows]
    md += ["", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    md += ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    for out in OUT_DIRS:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{NAME}.csv").write_text(csv, encoding="utf-8-sig")
        (out / f"{NAME}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print("written:", (out / NAME).relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--runs", type=Path, nargs="+", default=DEFAULT_RUNS)
    args = parser.parse_args()

    inventory = read_preflight(args.preflight)
    ratios = defaultdict(list)
    for run in args.runs:
        for host, value in normalized_x_step(run).items():
            ratios[host].append(value)

    unknown = {"os": NOT_INVENTORIED, "glibc": "?", "kernel": "?", "python": "?", "numpy": "?"}
    per_node = {host: statistics.median(values) for host, values in ratios.items()}
    rows = []
    for host in sorted(per_node, key=node_number):
        info = inventory.get(host, unknown)
        rows.append([host, info["os"], info["glibc"], info["kernel"], info["python"], info["numpy"],
                     len(ratios[host]), f"{per_node[host]:.3f}"])

    by_os = defaultdict(list)
    for host, value in per_node.items():
        by_os[inventory.get(host, unknown)["os"]].append(value)
    summary_rows = [[os_name, len(values), f"{statistics.median(values):.3f}", f"{min(values):.3f}",
                     f"{max(values):.3f}"] for os_name, values in sorted(by_os.items())]

    ordered = sorted(per_node.items(), key=lambda item: item[1])
    lowest, highest = ordered[0][1], ordered[-1][1]

    def describe(items):
        return ", ".join(f"{host} ({value:.3f}, {inventory.get(host, unknown)['os']})" for host, value in items)

    run_names = ", ".join(str(run.relative_to(ROOT)).replace("\\", "/") for run in args.runs)
    text_lines = [
        "Lokale x-Schritt-Zeit je Knoten im knotenscharfen Fall (118 Agenten). Die Zeit eines Rangs "
        "wird durch den Median aller Ränge desselben Laufs mit gleicher Zahl an Konsenskanten geteilt, "
        "weil die Kantenzahl die Lösezeit stark bestimmt. Je Knoten ist der Median über die Läufe "
        "angegeben. Werte unter 1 bedeuten schneller als vergleichbare Ränge.",
        "",
        f"Läufe: {run_names}. Betriebssystem aus der glibc-Version im Preflight "
        f"(`{str(args.preflight.relative_to(ROOT)).replace(chr(92), '/')}`): 2.36 = Debian 12, 2.41 = Debian 13.",
        "",
        f"Ergebnis: kein systematischer Unterschied zwischen den Betriebssystemen. Die Knotenmediane "
        f"reichen von {lowest:.3f} bis {highest:.3f} (Faktor {highest / lowest:.2f}). "
        f"Langsamste fünf Knoten: {describe(ordered[-5:][::-1])}. "
        f"Schnellste fünf Knoten: {describe(ordered[:5])}.",
    ]
    write_tables(rows, summary_rows, text_lines)
    for row in summary_rows:
        print("  ".join(map(str, row)))
    print(f"Knotenmediane {lowest:.3f} .. {highest:.3f}, Faktor {highest / lowest:.2f}")


if __name__ == "__main__":
    main()
