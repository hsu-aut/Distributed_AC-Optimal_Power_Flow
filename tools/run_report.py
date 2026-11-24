#!/usr/bin/env python3
"""Einzelauswertung je Lauf: Kennzahlentabelle, Rangtabelle und getrennte Residuendiagramme.

    python tools/run_report.py <lauf> [<lauf> ...] --out <ordner>
    python tools/run_report.py results/cluster/* --out results/cluster/einzelauswertung

Je Lauf entstehen (Dateiname = Laufname):
  <lauf>.md / <lauf>.csv            Kennzahlen des Laufs (eine Zeile je Größe)
  <lauf>.ranks.csv                  je Rang: Busse, lokale Optimierung, MPI, Warten
  <lauf>.residual_primal.png/.pdf   primales Residuum/Toleranz über die Iterationen
  <lauf>.residual_dual.png/.pdf     duales Residuum/Toleranz über die Iterationen
  <lauf>.residual.csv               die geplotteten Werte
Mit --absolute zusätzlich die unskalierten Residuen in p.u. als eigene Diagramme.

Ein Diagramm zeigt genau eine Größe eines Laufs, damit es ohne Nachbearbeitung in die Arbeit
übernommen werden kann. Gezeichnet werden das Abbruchkriterium des Lösers (Maximum über die
Ränge, dieses Kriterium beendet den Lauf) sowie die Boyd-Matrixform je Rang und global
(`docs/FORMULATION.md` §4, `docs/CRITERION_COMPARISON.md`). Die waagerechte Linie bei 1 ist die
Toleranz: darunter ist das jeweilige Kriterium erfüllt.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

CRITERIA = {
    "primal": [("max_rank_primal_ratio", "Löserkriterium (max. Rang)"),
               ("boyd_a_max_rank_primal_ratio", "Boyd-Matrixform (max. Rang)"),
               ("boyd_a_global_primal_ratio", "Boyd-Matrixform (global)")],
    "dual": [("max_rank_dual_ratio", "Löserkriterium (max. Rang)"),
             ("boyd_a_max_rank_dual_ratio", "Boyd-Matrixform (max. Rang)"),
             ("boyd_a_global_dual_ratio", "Boyd-Matrixform (global)")],
}
ABSOLUTE = {"primal": ("max_rank_primal_residual", "primales Residuum ‖x − z‖ [p.u.]"),
            "dual": ("max_rank_dual_residual", "duales Residuum ‖ρ Δz‖ [p.u.]")}
TITLES = {"primal": "Primales Residuum bezogen auf die Toleranz",
          "dual": "Duales Residuum bezogen auf die Toleranz"}


def phases(ranks):
    out = {}
    for rank in ranks:
        for name, value in rank["phases"].items():
            out[name] = out.get(name, 0.0) + value["total_s"]
    return out


def mpi_of(rank, split):
    p = rank["phases"]
    last = "convergence_mpi" if split else "convergence"
    return sum(p[n]["total_s"] for n in ("vt_exchange", "solve_status", "consensus_exchange", last) if n in p)


def thermal_for(run: Path):
    """Thermikdatei des Laufs, falls der Kampagnenstarter sie neben dem Ordner abgelegt hat."""
    path = run.parent / f"{run.name}.thermal.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    during = data.get("during") or {}
    samples = [row for rows in (during.get("samples") or {}).values() for row in rows]
    temps = [row[1] for row in samples]
    freqs = [row[3] for row in samples]
    capped = sum(1 for row in samples if int(row[2], 16) & 0x6)  # Bit 1/2: Takt gekappt bzw. gedrosselt
    soft = sum(1 for row in samples if int(row[2], 16) & 0x8)    # Bit 3: Softlimit Temperatur aktiv
    return {"Temperatur vor dem Lauf (max) [°C]": (data.get("before") or {}).get("max_temp_c"),
            "Temperatur während des Laufs (max) [°C]": during.get("max_temp_c"),
            "Temperatur während des Laufs (Median) [°C]": float(np.median(temps)) if temps else None,
            "Knoten mit Softlimit während des Laufs": len(during.get("nodes_throttled_during") or []),
            "Stichproben mit aktivem Softlimit [%]": round(100 * soft / len(samples), 1) if samples else None,
            "Stichproben mit gekapptem Takt [%]": round(100 * capped / len(samples), 1) if samples else None,
            "kleinster gemessener Takt [MHz]": min(freqs) if freqs else None,
            "Takt-Median unter Last [MHz]": (float(np.median(under_load))
                                             if (under_load := [f for t, f in zip(temps, freqs) if t >= 70]) else None)}


def figures(values, trace, col, kind, out: Path, name, absolute=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    iterations = trace[:, col["iteration"]]
    fig, ax = plt.subplots(figsize=(6.4, 3.8), constrained_layout=True)
    if absolute:
        key, label = ABSOLUTE[kind]
        ax.semilogy(iterations, trace[:, col[key]], linewidth=1.2, color="#1f4e79")
        ax.set_ylabel(label)
        ax.set_title(f"{'Primales' if kind == 'primal' else 'Duales'} Residuum, {name}")
        suffix = f"residual_{kind}_absolut"
    else:
        for (key, label), style in zip(CRITERIA[kind], ("-", "--", ":")):
            if key in col:
                ax.semilogy(iterations, trace[:, col[key]], style, linewidth=1.2, label=label)
        ax.axhline(1.0, color="black", linewidth=0.9, label="Toleranz (Quotient 1)")
        ax.set_ylabel("Residuum / Toleranz")
        ax.set_title(f"{TITLES[kind]}, {name}")
        if len(CRITERIA[kind]) > 1:
            ax.legend(fontsize=8, frameon=False)
        suffix = f"residual_{kind}"
    ax.set_xlabel("ADMM-Iteration")
    ax.grid(True, which="both", alpha=0.2)
    ax.set_xlim(0, iterations[-1])
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{suffix}.{ext}", dpi=200)
    plt.close(fig)


def report(run: Path, out: Path, absolute: bool):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    args, settings = manifest["args"], manifest["settings"]
    name = run.name
    values = {"Lauf": name, "Modus": "zentral" if args.get("central") else "verteilt",
              "Topologie": args.get("topology"), "Topologie-SHA256": manifest.get("topology_sha256", "")[:16],
              "Commit": manifest.get("commit", "")[:12], "Arbeitskopie verändert": manifest.get("dirty"),
              "Plattform": manifest.get("platform"), "Hostfile": manifest.get("hostfile"),
              "Startknoten": manifest.get("launcher_host"), "Gurobi-Threads": settings.get("GRB_THREADS"),
              "akzeptiert": result.get("success"), "Jobzeit [s]": round(result.get("wall_total_s", 0), 3)}
    if args.get("central"):
        central = result["central"]
        timing = central["timing"]
        values.update({"Erzeugungskosten": central["generation_cost"], "AC-Prüfung bestanden": central["raw_ac_feasible"],
                       "Gurobi-Kernzeit [s]": round(timing["gurobi_runtime"], 4),
                       "Gurobi-Status": timing["gurobi_status"], "Laden [s]": round(timing["python_wall_time_load"], 3),
                       "Modellaufbau [s]": round(timing["python_wall_time_build"], 3),
                       "nichtlineare Nebenbedingungen": timing.get("n_nonlinear_constraints"),
                       "PWL-Nebenbedingungen": timing.get("n_pwl_constraints")})
        trace = None
    else:
        admm = result["admm"]
        validation = json.loads((run / "logs" / "ac_validation.json").read_text(encoding="utf-8"))
        ranks = json.loads((run / "logs" / "rank_metrics.json").read_text(encoding="utf-8"))["ranks"]
        ph = phases(ranks)
        split = "convergence_mpi" in ph
        iterations = admm["wall_iterations"]
        loop = admm["wall_iterations_s"]
        mpi_total = sum(mpi_of(r, split) for r in ranks)
        local_admm = ph.get("zu_update", 0.0) + (ph.get("convergence", 0.0) if split else 0.0)
        output = ph.get("io", 0.0) + ph.get("monitor", 0.0) + ph.get("console", 0.0)
        rank_sum = loop * len(ranks)
        buses = [len(r["bus_ids"]) for r in ranks]
        x_each = [r["phases"]["x_solve"]["total_s"] for r in ranks]
        values.update({
            "Agenten / MPI-Ränge": f"{args['agents']} / {len(ranks)}",
            "Partitionierer": (manifest.get("partition") or {}).get("strategy", "buswise"),
            "ρ": args["rho"], "Kantengewichtung": args.get("edge_rho"),
            "Kantenexponent": args.get("edge_rho_exponent") if args.get("edge_rho") != "none" else None,
            "Toleranz ε_abs = ε_rel": args["abs_tol"], "Iterationsbudget": args["iterations"],
            "Iterationen": iterations, "Budgetausnutzung [%]": round(100 * iterations / args["iterations"], 1),
            "konvergiert": admm["converged"], "AC-Prüfung bestanden": admm["raw_ac_feasible"],
            "Erzeugungskosten": validation["generation_cost"],
            "max. Bilanzfehler ΔP [p.u.]": validation["max_dP"],
            "max. Bilanzfehler ΔQ [p.u.]": validation["max_dQ"],
            "max. Spannungsverletzung": validation["max_voltage_violation"],
            "max. thermische Verletzung": validation["max_thermal_violation"],
            "Schleifenzeit [s]": round(loop, 3), "ms je Iteration": round(1000 * loop / iterations, 3),
            "CPU-Summe aller Ränge [s]": round(admm["cpu_rank_sum_s"], 1),
            "Busse je Teilproblem (min/Median/max)": f"{min(buses)}/{int(np.median(buses))}/{max(buses)}",
            "lokale Optimierung [% der Rang-Schleifenzeit]": round(100 * ph["x_solve"] / rank_sum, 1),
            "MPI inkl. Warten [%]": round(100 * mpi_total / rank_sum, 1),
            "lokale ADMM-Rechnung [%]": round(100 * local_admm / rank_sum, 1),
            "Ausgabe und Beobachtung [%]": round(100 * output / rank_sum, 1),
            "nicht zugeordnet [%]": round(100 * (rank_sum - ph["x_solve"] - mpi_total - local_admm - output) / rank_sum, 1),
            "lokale Optimierung [ms je Iteration und Rang]": round(1000 * ph["x_solve"] / iterations / len(ranks), 3),
            "MPI [ms je Iteration und Rang]": round(1000 * mpi_total / iterations / len(ranks), 3),
            "Lastspreizung x-Schritt (max/min)": round(max(x_each) / max(1e-9, min(x_each)), 2),
            "MPI-Aufrufe je Iteration und Rang": round(sum(v["calls"] for r in ranks for v in r["mpi"].values()) / iterations / len(ranks), 2),
            "MPI-Nutzdaten [B je Iteration und Rang]": round(sum(v["input_buffer_bytes"] for r in ranks for v in r["mpi"].values()) / iterations / len(ranks)),
            "Zeitmodell": "getrennt" if split else "alt (Konvergenzphase enthält Rechnung und Ausgabe)",
        })
        boyd = admm.get("boyd_consensus_final") or {}
        if boyd:
            values.update({"Boyd-Matrixform am Ende: primal": round(boyd.get("primal_ratio", float("nan")), 4),
                           "Boyd-Matrixform am Ende: dual": round(boyd.get("dual_ratio", float("nan")), 4)})
        with open(out / f"{name}.ranks.csv", "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["rang", "busse", "x_solve_s", "mpi_s", "x_solve_ms_je_iteration", "mpi_ms_je_iteration"])
            for r in ranks:
                writer.writerow([r["rank"], len(r["bus_ids"]), round(r["phases"]["x_solve"]["total_s"], 3),
                                 round(mpi_of(r, split), 3), round(1000 * r["phases"]["x_solve"]["total_s"] / iterations, 3),
                                 round(1000 * mpi_of(r, split) / iterations, 3)])
        trace_file = run / "logs" / "residual_trace.npy"
        trace = None
        if trace_file.exists():
            columns = json.loads((run / "logs" / "residual_trace_columns.json").read_text(encoding="utf-8"))
            trace = np.load(trace_file)
            col = {c: i for i, c in enumerate(columns)}
            for kind in ("primal", "dual"):
                figures(values, trace, col, kind, out, name)
                if absolute:
                    figures(values, trace, col, kind, out, name, absolute=True)
            with open(out / f"{name}.residual.csv", "w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(columns)
                for row in trace:
                    writer.writerow([f"{v:.10g}" for v in row])
    values.update(thermal_for(run))

    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Größe", "Wert"])
        for key, value in values.items():
            writer.writerow([key, value])
    lines = [f"# Einzelauswertung {name}", "", "| Größe | Wert |", "|---|---|"]
    lines += [f"| {k} | {'' if v is None else v} |" for k, v in values.items()]
    if trace is not None:
        lines += ["", f"Diagramme: `{name}.residual_primal.png`, `{name}.residual_dual.png`;",
                  f"Werte: `{name}.residual.csv`, Rangtabelle: `{name}.ranks.csv`."]
    (out / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path, help="Laufordner (mit manifest.json und result.json)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--absolute", action="store_true", help="zusätzlich Diagramme der unskalierten Residuen")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    done = []
    for run in args.runs:
        if not (run / "manifest.json").exists() or not (run / "result.json").exists():
            print(f"übersprungen (kein vollständiger Lauf): {run}")
            continue
        done.append(report(run, args.out, args.absolute))
        print(f"{run.name}: Tabelle und Diagramme geschrieben")
    if done:
        keys = list(dict.fromkeys(k for v in done for k in v))
        with open(args.out / "uebersicht.csv", "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=keys, delimiter=";")
            writer.writeheader()
            writer.writerows(done)
        print(f"{len(done)} Läufe -> {args.out} (Übersicht: uebersicht.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
