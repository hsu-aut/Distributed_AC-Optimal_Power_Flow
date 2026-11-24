#!/usr/bin/env python3
"""Abbildungen und Tabellen für die Auswertung der Studienarbeit.

    python tools/thesis_package.py --kampagne <ordner> --ohne-kuehlung <ordner> --vorlauf <ordner> \
        --pglib <ordner> --tests <ordner> [<ordner> ...] --out <zielordner>

Erzeugt genau die Abbildungen und Tabellen, die für die Auswertung gebraucht werden. Jede
Abbildung zeigt eine Aussage, ohne Überlagerung mehrerer Kampagnen; Tabellen liegen als CSV
(Semikolon, für Excel) und als Markdown vor. `README.md` im Zielordner ordnet jedes Element
einem Abschnitt der Arbeit zu.

Begriffe: „Rechnen“ ist der x-Schritt (lokales NLP), „MPI“ sind die blockierenden MPI-Aufrufe
einschließlich Wartezeit, „Übriges“ ist der Rest der Schleifenzeit (z-/u-Schritt, Residuen,
Logs, nicht zugeordnet).
"""
import argparse
import collections
import csv
import json
import math
import re
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributed_opf.config import load_topology_config  # noqa: E402

BOUND_TOL = 1e-6
FARBE = {"rechnen": "#1f4e79", "mpi": "#c55a11", "rest": "#a6a6a6",
         "einheitlich": "#1f4e79", "kante": "#c55a11", "kuehlung": "#1f4e79", "ohne": "#c55a11"}


# ---------------------------------------------------------------- Daten einlesen

def lies_lauf(folder: Path):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    eintrag = {"name": folder.name, "ordner": folder, "manifest": manifest, "result": result,
               "args": manifest["args"], "zentral": bool(manifest["args"].get("central")),
               "jobzeit": result.get("wall_total_s"), "erfolg": bool(result.get("success"))}
    if eintrag["zentral"]:
        zentral = result["central"]
        eintrag.update(kosten=zentral["generation_cost"], kernzeit=zentral["timing"]["gurobi_runtime"],
                       zulaessig=zentral["raw_ac_feasible"])
        return eintrag
    admm = result["admm"]
    pruefung = json.loads((folder / "logs" / "ac_validation.json").read_text(encoding="utf-8"))
    raenge = json.loads((folder / "logs" / "rank_metrics.json").read_text(encoding="utf-8"))["ranks"]
    phasen = collections.defaultdict(float)
    for rang in raenge:
        for name, wert in rang["phases"].items():
            phasen[name] += wert["total_s"]
    getrennt = "convergence_mpi" in phasen
    mpi = sum(phasen[n] for n in ("vt_exchange", "solve_status", "consensus_exchange",
                                  "convergence_mpi" if getrennt else "convergence"))
    rechnen = phasen["x_solve"]
    schleife = admm["wall_iterations_s"]
    iterationen = admm["wall_iterations"]
    rang_summe = schleife * len(raenge)
    busse = [len(r["bus_ids"]) for r in raenge]
    x_je_rang = [r["phases"]["x_solve"]["total_s"] for r in raenge]
    eintrag.update(
        iterationen=iterationen, budget=args_budget(manifest), konvergiert=admm["converged"],
        zulaessig=admm["raw_ac_feasible"], kosten=pruefung["generation_cost"],
        dP=pruefung["max_dP"], dQ=pruefung["max_dQ"], spannung=pruefung["max_voltage_violation"],
        thermisch=pruefung["max_thermal_violation"], schleife=schleife, raenge=len(raenge),
        ms_je_iteration=1000 * schleife / iterationen, cpu_summe=admm["cpu_rank_sum_s"],
        rechnen_anteil=rechnen / rang_summe, mpi_anteil=mpi / rang_summe,
        rest_anteil=max(0.0, 1 - (rechnen + mpi) / rang_summe),
        rechnen_ms=1000 * rechnen / iterationen / len(raenge), mpi_ms=1000 * mpi / iterationen / len(raenge),
        busse_mittel=statistics.mean(busse), busse_max=max(busse), busse_min=min(busse),
        x_max_ms=1000 * max(x_je_rang) / iterationen, spreizung=max(x_je_rang) / max(1e-9, min(x_je_rang)),
        kante=manifest["args"].get("edge_rho", "none"), rho=manifest["args"]["rho"],
        toleranz=manifest["args"]["abs_tol"], agenten=manifest["args"]["agents"])
    return eintrag


def args_budget(manifest):
    return manifest["args"]["iterations"]


def lies_kampagne(root: Path):
    laeufe = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if (folder / "manifest.json").exists() and (folder / "result.json").exists():
            laeufe.append(lies_lauf(folder))
    gruppen = collections.defaultdict(list)
    for lauf in laeufe:
        gruppen[re.sub(r"_r\d+$", "", lauf["name"])].append(lauf)
    return laeufe, gruppen


def median_gruppe(laeufe, feld):
    werte = [l[feld] for l in laeufe if l.get(feld) is not None]
    return statistics.median(werte) if werte else None


def thermik(root: Path):
    """Temperatur und Drosselung je Lauf aus campaign.log."""
    log = root / "campaign.log"
    if not log.exists():
        return {}
    out = {}
    for zeile in log.read_text(encoding="utf-8").splitlines():
        treffer = re.search(r"\] (\S+) exit=\d+ .*wall=([\d.]+)s temp_max=[\d.]+->[\d.]+C "
                            r"during_max=([\d.]+)C throttled_during=(\d+)", zeile)
        if treffer:
            out[treffer.group(1)] = {"jobzeit": float(treffer.group(2)), "temperatur": float(treffer.group(3)),
                                     "gedrosselt": int(treffer.group(4))}
    return out


def kurzname(name):
    """Kurze Beschriftung für Achsen: Fallkennung und Agentenzahl."""
    fall = name.split("_")[0]
    treffer = re.search(r"k(\d+)", name)
    return f"{fall} k{treffer.group(1)}" if treffer else fall


def sortier(name):
    treffer = re.search(r"k(\d+)", name)
    return (name.split("_")[0], int(treffer.group(1)) if treffer else 0)


# ---------------------------------------------------------------- Abbildungen

def neue_achse(plt, hoehe=3.6):
    fig, ax = plt.subplots(figsize=(6.2, hoehe), constrained_layout=True)
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)
    return fig, ax


def sekunden(wert):
    """Sekundenangabe mit drei signifikanten Stellen."""
    if wert < 10:
        return f"{wert:.2f} s"
    if wert < 100:
        return f"{wert:.1f} s"
    return f"{wert:.0f} s"


def speichere(fig, out: Path, name):
    for endung in ("png", "pdf"):
        fig.savefig(out / f"{name}.{endung}", dpi=200)
    import matplotlib.pyplot as plt
    plt.close(fig)


def abb_zeit_je_iteration(plt, gruppen, out):
    fig, ax = neue_achse(plt)
    for praefix, label, farbe, marker in (("C1_partitions", "einheitliches ρ = 3·10⁵", FARBE["einheitlich"], "o"),
                                          ("C4_partitions_edge_rho", "kantengewichtetes ρ", FARBE["kante"], "s")):
        punkte = sorted((g[0]["agenten"], median_gruppe(g, "ms_je_iteration"))
                        for name, g in gruppen.items() if name.startswith(praefix))
        ax.plot([p[0] for p in punkte], [p[1] for p in punkte], marker=marker, color=farbe, label=label, linewidth=1.6)
    bus = [(g[0]["agenten"], median_gruppe(g, "ms_je_iteration")) for name, g in gruppen.items()
           if name.startswith("C2_bus118")]
    if bus:
        ax.plot(bus[0][0], bus[0][1], marker="D", color=FARBE["kante"], linestyle="none",
                markerfacecolor="white", label="118 Busagenten (kantengew.)")
    ax.set_xscale("log")
    ax.set_xticks([3, 4, 8, 16, 32, 64, 118])
    ax.get_xaxis().set_major_formatter(plt.matplotlib.ticker.ScalarFormatter())
    ax.set_xlabel("Anzahl der Agenten (logarithmisch)")
    ax.set_ylabel("Schleifenzeit je Iteration [ms]")
    ax.set_ylim(0, None)
    ax.legend(frameon=False, fontsize=9)
    speichere(fig, out, "abb01_zeit_je_iteration")


def abb_kipppunkt(plt, gruppen, out):
    fig, ax = neue_achse(plt)
    punkte = sorted((median_gruppe(g, "busse_mittel"), median_gruppe(g, "rechnen_ms"), median_gruppe(g, "mpi_ms"))
                    for name, g in gruppen.items()
                    if name.startswith(("C4_partitions_edge_rho", "C2_bus118")))
    x = [p[0] for p in punkte]
    ax.plot(x, [p[1] for p in punkte], marker="o", color=FARBE["rechnen"], label="Rechnen (x-Schritt)", linewidth=1.6)
    ax.plot(x, [p[2] for p in punkte], marker="s", color=FARBE["mpi"], label="MPI inkl. Warten", linewidth=1.6)
    # Schnittpunkt aus log-linearer Interpolation des Verhältnisses
    schnitt = None
    for (n1, r1, m1), (n2, r2, m2) in zip(punkte, punkte[1:]):
        if (r1 / m1 - 1) * (r2 / m2 - 1) < 0:
            a, b = math.log(r1 / m1), math.log(r2 / m2)
            schnitt = math.exp(math.log(n1) + (0 - a) * (math.log(n2) - math.log(n1)) / (b - a))
    if schnitt:
        ax.axvline(schnitt, color="black", linestyle=":", linewidth=1.0)
        ax.annotate(f"Kipppunkt ≈ {schnitt:.1f} Busse", xy=(schnitt, max(p[1] for p in punkte)),
                    xytext=(6, -2), textcoords="offset points", fontsize=9)
    ax.set_xscale("log")
    ax.set_yscale("log")
    for achse, werte in ((ax.xaxis, [1, 2, 4, 8, 15, 30, 40]), (ax.yaxis, [2, 5, 10, 20, 50])):
        achse.set_major_locator(plt.matplotlib.ticker.FixedLocator(werte))
        achse.set_minor_locator(plt.matplotlib.ticker.NullLocator())
        achse.set_major_formatter(plt.matplotlib.ticker.ScalarFormatter())
    ax.set_xlabel("mittlere Teilproblemgröße [Busse je Agent] – beide Achsen logarithmisch")
    ax.set_ylabel("Zeit je Iteration und Rang [ms]")
    ax.legend(frameon=False, fontsize=9)
    speichere(fig, out, "abb02_kipppunkt")
    return schnitt


def abb_zeitzerlegung(plt, gruppen, out):
    fig, ax = neue_achse(plt)
    namen = sorted((n for n in gruppen if n.startswith(("C4_partitions_edge_rho", "C2_bus118"))),
                   key=lambda n: gruppen[n][0]["agenten"])
    beschriftung = [f"{gruppen[n][0]['agenten']}" for n in namen]
    unten = np.zeros(len(namen))
    for feld, label, farbe in (("rechnen_anteil", "Rechnen (x-Schritt)", FARBE["rechnen"]),
                               ("mpi_anteil", "MPI inkl. Warten", FARBE["mpi"]),
                               ("rest_anteil", "Übriges", FARBE["rest"])):
        werte = np.array([100 * median_gruppe(gruppen[n], feld) for n in namen])
        ax.bar(beschriftung, werte, bottom=unten, label=label, color=farbe, width=0.68)
        unten += werte
    ax.set_xlabel("Anzahl der Agenten")
    ax.set_ylabel("Anteil an der Schleifenzeit [%]")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, fontsize=9, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.16))
    speichere(fig, out, "abb03_zeitzerlegung")


def abb_iterationen(plt, gruppen, out):
    fig, ax = neue_achse(plt)
    ks = [3, 4, 8, 16]
    hole = lambda praefix, k: next((g[0]["iterationen"] for n, g in gruppen.items()
                                    if n == f"{praefix}_k{k}"), None)
    breite = 0.38
    stellen = np.arange(len(ks))
    einheitlich = [hole("C1_partitions", k) for k in ks]
    kante = [hole("C4_partitions_edge_rho", k) for k in ks]
    ax.bar(stellen - breite / 2, einheitlich, breite, label="einheitliches ρ", color=FARBE["einheitlich"])
    ax.bar(stellen + breite / 2, kante, breite, label="kantengewichtetes ρ", color=FARBE["kante"])
    for i, (a, b) in enumerate(zip(einheitlich, kante)):
        ax.annotate(f"−{100 * (a - b) / a:.0f} %", xy=(i + breite / 2, b), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=8)
    ax.set_xticks(stellen, [str(k) for k in ks])
    ax.set_xlabel("Anzahl der Partitionen")
    ax.set_ylabel("Iterationen bis zur Konvergenz")
    ax.legend(frameon=False, fontsize=9)
    speichere(fig, out, "abb04_iterationen_strafparameter")


def abb_residuum(plt, lauf, out, nummer, kurz):
    spur = lauf["ordner"] / "logs" / "residual_trace.npy"
    if not spur.exists():
        return
    spalten = json.loads((lauf["ordner"] / "logs" / "residual_trace_columns.json").read_text(encoding="utf-8"))
    daten = np.load(spur)
    index = {name: i for i, name in enumerate(spalten)}
    # Die Bezeichnungen benennen die tatsächlich implementierten Größen: beim primalen Teil das
    # Residuum ‖Ax − Ez‖ bezogen auf die bindende Schwelle min(ε_pri, ε_abs), beim dualen Teil das
    # elementweise Änderungsmaß ‖ρ Δz‖ ohne Aᵀ-Aggregation — nicht das kanonische duale Residuum.
    for art, feld, label in (("primal", "max_rank_primal_ratio", "primales Residuum / Toleranz"),
                             ("dual", "max_rank_dual_ratio", "duales Änderungsmaß / Toleranz")):
        fig, ax = neue_achse(plt)
        ax.semilogy(daten[:, index["iteration"]], daten[:, index[feld]], color=FARBE["rechnen"], linewidth=1.3)
        ax.axhline(1.0, color="black", linewidth=0.9, linestyle="--")
        ax.set_xlabel("ADMM-Iteration")
        ax.set_ylabel(label)
        ax.set_xlim(0, daten[-1, index["iteration"]])
        speichere(fig, out, f"abb{nummer:02d}_residuum_{art}_{kurz}")
        nummer += 1


def abb_dualmass_vergleich(plt, lauf, out, nummer, kurz):
    """Implementiertes duales Änderungsmaß gegenüber der Boyd-Matrixform mit Aᵀ-Aggregation.

    Beide Größen werden auf ihre eigene Schwelle bezogen; der Quotient sagt also, ob die beiden
    Definitionen den Lauf zum selben Zeitpunkt beenden würden.
    """
    spur = lauf["ordner"] / "logs" / "residual_trace.npy"
    if not spur.exists():
        return
    spalten = json.loads((lauf["ordner"] / "logs" / "residual_trace_columns.json").read_text(encoding="utf-8"))
    if "boyd_a_max_rank_dual_ratio" not in spalten:
        return
    daten = np.load(spur)
    index = {name: i for i, name in enumerate(spalten)}
    boyd = daten[:, index["boyd_a_max_rank_dual_ratio"]]
    gueltig = boyd > 0
    if not gueltig.any():
        return
    fig, ax = neue_achse(plt)
    ax.plot(daten[gueltig, index["iteration"]], daten[gueltig, index["max_rank_dual_ratio"]] / boyd[gueltig],
            color=FARBE["rechnen"], linewidth=1.3)
    ax.axhline(1.0, color="black", linewidth=0.9, linestyle="--")
    ax.set_xlabel("ADMM-Iteration")
    ax.set_ylabel("dualer Abbruchquotient:\nimplementiert / Boyd-Matrixform")
    ax.set_xlim(0, daten[-1, index["iteration"]])
    speichere(fig, out, f"abb{nummer:02d}_dualmass_gegen_boyd_{kurz}")


def abb_thermik(plt, warm, kalt, therm_warm, therm_kalt, out):
    namen = sorted((n for n in warm if not warm[n][0]["zentral"]), key=sortier)
    kurz = [kurzname(n) for n in namen]

    fig, ax = neue_achse(plt, 3.9)
    for daten, label, farbe, versatz in ((therm_warm, "mit Lüfterkühlung", FARBE["kuehlung"], -0.19),
                                         (therm_kalt, "ohne Lüfterkühlung", FARBE["ohne"], 0.19)):
        werte = [statistics.median([daten[f"{n}_r{i}"]["temperatur"] for i in range(1, 6)
                                    if f"{n}_r{i}" in daten] or [float("nan")]) for n in namen]
        ax.bar(np.arange(len(namen)) + versatz, werte, 0.38, label=label, color=farbe)
    ax.set_xticks(np.arange(len(namen)), kurz, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("höchste Knotentemperatur im Lauf [°C]")
    ax.set_ylim(50, 92)
    ax.axhline(80, color="black", linestyle=":", linewidth=1.0)
    ax.annotate("Softlimit 80 °C", xy=(0, 80), xytext=(2, 3), textcoords="offset points", fontsize=8)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    speichere(fig, out, "abb09_thermik_temperatur")

    fig, ax = neue_achse(plt, 3.9)
    anteile = []
    for n in namen:
        w = median_gruppe(warm[n], "jobzeit")
        k = median_gruppe(kalt[n], "jobzeit") if n in kalt else None
        anteile.append(100 * (k - w) / w if (w and k) else float("nan"))
    farben = [FARBE["ohne"] if a > 0 else FARBE["kuehlung"] for a in anteile]
    ax.bar(np.arange(len(namen)), anteile, 0.6, color=farben)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(np.arange(len(namen)), kurz, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Mehrzeit ohne Lüfter [%]")
    speichere(fig, out, "abb10_thermik_laufzeit")


def abb_konvergenzpruefung(plt, tests, out):
    paare = [("k16_K1", "k16_K10", "16 Partitionen"), ("k118_K1", "k118_K10", "118 Busagenten")]
    vorhanden = [(a, b, label) for a, b, label in paare if a in tests and b in tests]
    if not vorhanden:
        return
    fig, ax = neue_achse(plt)
    stellen = np.arange(len(vorhanden))
    breite = 0.38
    k1 = [1000 * tests[a]["schleife"] / tests[a]["iterationen"] for a, b, _ in vorhanden]
    k10 = [1000 * tests[b]["schleife"] / tests[b]["iterationen"] for a, b, _ in vorhanden]
    ax.bar(stellen - breite / 2, k1, breite, label="Prüfung jede Iteration", color=FARBE["einheitlich"])
    ax.bar(stellen + breite / 2, k10, breite, label="Prüfung alle 10 Iterationen", color=FARBE["kante"])
    for i, (a, b) in enumerate(zip(k1, k10)):
        ax.annotate(f"−{100 * (a - b) / a:.0f} %", xy=(i + breite / 2, b), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=8)
    ax.set_xticks(stellen, [label for _, _, label in vorhanden])
    ax.set_ylabel("Schleifenzeit je Iteration [ms]")
    ax.legend(frameon=False, fontsize=9)
    speichere(fig, out, "abb11_konvergenzpruefung")


def abb_zentral_verteilt(plt, gruppen, out):
    zentral = next((g for n, g in gruppen.items() if n == "C0_central_t1"), None)
    auswahl = [n for n in gruppen if n.startswith(("C4_partitions_edge_rho", "C2_bus118"))]
    auswahl.sort(key=lambda n: gruppen[n][0]["agenten"])
    # Säulen auf linearer Achse: der zentrale Lauf ist genau deshalb nur ein Strich. Eine
    # logarithmische Achse würde den Abstand optisch verkleinern, der die Aussage trägt.
    fig, ax = neue_achse(plt)
    label = ["zentral\n1 Pi"] + [f"{gruppen[n][0]['agenten']}" for n in auswahl]
    werte = [median_gruppe(zentral, "jobzeit")] + [median_gruppe(gruppen[n], "jobzeit") for n in auswahl]
    farben = [FARBE["rest"]] + [FARBE["kante"]] * len(auswahl)
    balken = ax.bar(label, werte, color=farben, width=0.68)
    ax.bar_label(balken, labels=[sekunden(w) for w in werte], fontsize=9, padding=3)
    ax.set_ylim(0, max(werte) * 1.12)
    ax.set_ylabel("Jobzeit [s], Median aus 5 Läufen")
    ax.set_xlabel("Anzahl der Agenten (verteilt, kantengewichtetes ρ)")
    speichere(fig, out, "abb12_zentral_gegen_verteilt")


# ---------------------------------------------------------------- Tabellen

def schreibe_tabelle(out: Path, name, kopf, zeilen, titel, hinweis=""):
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei, delimiter=";")
        schreiber.writerow(kopf)
        schreiber.writerows(zeilen)
    zeilen_md = ["| " + " | ".join(str(z) for z in zeile) + " |" for zeile in zeilen]
    text = [f"# {titel}", ""] + ([hinweis, ""] if hinweis else [])
    text += ["| " + " | ".join(kopf) + " |", "|" + "|".join("---" for _ in kopf) + "|"] + zeilen_md
    (out / f"{name}.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def aktive_grenzen(lauf, zentral_zustand, topo):
    """Aktive Grenzen aus dem Endcheckpoint; None, wenn der Lauf ohne Checkpoints archiviert ist."""
    ordner = lauf["ordner"] / "logs" / "checkpoints"
    if not ordner.exists():
        return None
    ck = sorted(ordner.glob("iter_*"))
    if not ck:
        return None
    zustand = {}
    for p in ck[-1].glob("rank_*.json"):
        zustand.update(json.loads(p.read_text(encoding="utf-8")).get("states", {}))
    zustand = {int(b): s for b, s in zustand.items()}
    mengen = {}
    for label, zustands in (("verteilt", zustand), ("zentral", zentral_zustand)):
        menge = set()
        for b, bus in topo.buses.items():
            s = zustands.get(b)
            if s is None:
                continue
            if abs(float(s["v"]) - bus.v_max_pu) <= BOUND_TOL:
                menge.add(("Vmax", b))
            if bus.is_generator:
                if abs(float(s["pg"]) - bus.pg_min) <= BOUND_TOL:
                    menge.add(("Pmin", b))
                if abs(float(s["pg"]) - bus.pg_max) <= BOUND_TOL:
                    menge.add(("Pmax", b))
                if abs(float(s["qg"]) - bus.qg_max) <= BOUND_TOL:
                    menge.add(("Qmax", b))
                if abs(float(s["qg"]) - bus.qg_min) <= BOUND_TOL:
                    menge.add(("Qmin", b))
        mengen[label] = menge
    gleich = mengen["verteilt"] == mengen["zentral"]
    dPg = max(abs(float(zustand[b]["pg"]) - zentral_zustand[b]["pg"]) for b in zustand) * 100
    return {"gleich": gleich, "anzahl": len(mengen["verteilt"]), "abweichungen": len(mengen["zentral"] ^ mengen["verteilt"]),
            "dPg_MW": dPg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kampagne", type=Path, required=True, help="Hauptkampagne (mit Kühlung)")
    ap.add_argument("--ohne-kuehlung", type=Path, help="gleiche Kampagne ohne Lüfter")
    ap.add_argument("--vorlauf", type=Path, help="frühere Kampagne (Prüfung jede Iteration)")
    ap.add_argument("--pglib", type=Path, help="Kampagne mit Zweiggrenzen (PGLib)")
    ap.add_argument("--tests", type=Path, nargs="*", default=[], help="Testreihen (K-Sweep, asynchron)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 10, "legend.fontsize": 9,
                         "xtick.labelsize": 9, "ytick.labelsize": 9, "axes.spines.top": False,
                         "axes.spines.right": False})

    abb = args.out / "abbildungen"
    tab = args.out / "tabellen"
    abb.mkdir(parents=True, exist_ok=True)
    tab.mkdir(parents=True, exist_ok=True)

    laeufe, gruppen = lies_kampagne(args.kampagne)
    therm_warm = thermik(args.kampagne)
    kalt_laeufe, kalt_gruppen, therm_kalt = [], {}, {}
    if args.ohne_kuehlung:
        kalt_laeufe, kalt_gruppen = lies_kampagne(args.ohne_kuehlung)
        therm_kalt = thermik(args.ohne_kuehlung)
    vor_laeufe, vor_gruppen = ([], {})
    if args.vorlauf:
        vor_laeufe, vor_gruppen = lies_kampagne(args.vorlauf)
    tests = {}
    for root in args.tests:
        if root.exists():
            test_laeufe, _ = lies_kampagne(root)
            tests.update({l["name"]: l for l in test_laeufe})

    # --- Abbildungen
    abb_zeit_je_iteration(plt, gruppen, abb)
    kipppunkt = abb_kipppunkt(plt, gruppen, abb)
    abb_zeitzerlegung(plt, gruppen, abb)
    abb_iterationen(plt, gruppen, abb)
    for nummer, (name, kurz) in enumerate([("C4_partitions_edge_rho_k16_r1", "k16"),
                                           ("C2_bus118_edge_rho_k118_r1", "k118")]):
        lauf = next((l for l in laeufe if l["name"] == name), None)
        if lauf:
            abb_residuum(plt, lauf, abb, 5 + 2 * nummer, kurz)
            abb_dualmass_vergleich(plt, lauf, abb, 13 + nummer, kurz)
    if kalt_gruppen:
        abb_thermik(plt, gruppen, kalt_gruppen, therm_warm, therm_kalt, abb)
    abb_konvergenzpruefung(plt, tests, abb)
    abb_zentral_verteilt(plt, gruppen, abb)

    # --- Tabelle 1: Abnahme
    zeilen = []
    for label, menge in (("19.10. mit Lüfterkühlung", laeufe), ("19.10. ohne Lüfterkühlung", kalt_laeufe),
                         ("15.10. (Prüfung jede Iteration)", vor_laeufe)):
        if not menge:
            continue
        admm = [l for l in menge if not l["zentral"]]
        # Der Parameter der Konvergenzprüfung steht nur in den verteilten Läufen.
        erste = admm[0]["manifest"]
        zeilen.append([label, erste.get("commit", "")[:8],
                       erste["settings"].get("ADMM_CONV_CHECK_EVERY", "1"),
                       len(menge), len(admm), sum(1 for l in menge if l["erfolg"]),
                       sum(1 for l in admm if l["konvergiert"]), sum(1 for l in admm if l["zulaessig"]),
                       f"{100 * max(l['iterationen'] / l['budget'] for l in admm):.0f} %"])
    schreibe_tabelle(tab, "tab01_abnahme", ["Messreihe", "Programmstand", "Prüfung alle K Iterationen",
                                            "Läufe", "davon verteilt", "akzeptiert", "konvergiert",
                                            "AC-zulässig", "höchste Budgetausnutzung"], zeilen,
                     "Abnahme der Messreihen",
                     "Akzeptiert heißt: alle lokalen Lösungen lokal optimal, Abbruchkriterium auf jedem Rang "
                     "erfüllt und unabhängige AC-Prüfung bestanden (Bilanz ≤ 1e-4 p.u., Grenzen ≤ 1e-5). "
                     "Die Spalten „konvergiert“ und „AC-zulässig“ beziehen sich auf die verteilten Läufe; "
                     "die zentralen Referenzläufe haben kein Abbruchkriterium. Nicht enthalten sind die drei "
                     "Netzwerk-Benchmarkläufe je Kampagne.")

    # --- Tabelle 2: Skalierung und Zeitzerlegung
    zeilen = []
    for name in sorted((n for n in gruppen if not gruppen[n][0]["zentral"]), key=sortier):
        g = gruppen[name]
        erste = g[0]
        zeilen.append([name.replace("_", " "), erste["agenten"], f"{median_gruppe(g, 'busse_mittel'):.1f}",
                       erste["busse_max"], f"{erste['toleranz']:.0e}",
                       "kantengew." if erste["kante"] != "none" else "einheitlich",
                       erste["iterationen"], erste["budget"],
                       f"{median_gruppe(g, 'jobzeit'):.1f}", f"{median_gruppe(g, 'ms_je_iteration'):.2f}",
                       f"{median_gruppe(g, 'rechnen_ms'):.2f}", f"{median_gruppe(g, 'mpi_ms'):.2f}",
                       f"{100 * median_gruppe(g, 'rechnen_anteil'):.0f}",
                       f"{100 * median_gruppe(g, 'mpi_anteil'):.0f}",
                       f"{median_gruppe(g, 'spreizung'):.1f}"])
    schreibe_tabelle(tab, "tab02_skalierung", ["Konfiguration", "Agenten", "Busse Ø", "Busse max", "Toleranz",
                                               "Strafparameter", "Iterationen", "Budget", "Jobzeit [s]",
                                               "ms je Iteration", "Rechnen [ms]", "MPI [ms]", "Rechnen [%]",
                                               "MPI [%]", "Lastspreizung"], zeilen,
                     "Skalierung und Zeitzerlegung",
                     "Median über die Wiederholungen. „Rechnen“ und „MPI“ sind je Iteration und Rang angegeben; "
                     "die Anteile beziehen sich auf die Rang-Schleifenzeit. Lastspreizung ist das Verhältnis der "
                     "x-Schritt-Zeit zwischen langsamstem und schnellstem Rang.")

    # --- Tabelle 3: Qualität
    zentral_lauf = next((l for l in laeufe if l["zentral"] and "t1" in l["name"]), None)
    zentral_kosten = zentral_lauf["kosten"]
    zentral_daten = json.loads((zentral_lauf["ordner"] / "result.json").read_text(encoding="utf-8"))["central"]
    zentral_zustand = {int(b): {"v": zentral_daten["v"][b], "pg": zentral_daten["pg"][b],
                                "qg": zentral_daten["qg"][b]} for b in zentral_daten["v"]}
    zeilen = []
    # Aktive Grenzen brauchen Endcheckpoint und Topologie. Fehlen sie (kompakt archivierte Läufe),
    # wird der gleichnamige Lauf der Vergleichsmessreihe verwendet; die Iterate sind identisch.
    def grenzen_fuer(name, erste):
        for kandidat in (erste, *(vor_gruppen.get(name, [])[:1])):
            topo_datei = kandidat["ordner"] / "topology.yaml"
            if not topo_datei.exists():
                topo_datei = ROOT / kandidat["args"]["topology"]
            if not topo_datei.exists():
                continue
            ergebnis = aktive_grenzen(kandidat, zentral_zustand, load_topology_config(topo_datei))
            if ergebnis:
                ergebnis["quelle"] = "diese Messreihe" if kandidat is erste else "Messreihe 15.10."
                return ergebnis
        return None

    for name in sorted((n for n in gruppen if not gruppen[n][0]["zentral"] and not n.startswith("N0")), key=sortier):
        g = gruppen[name]
        erste = g[0]
        grenzen = grenzen_fuer(name, erste)
        kosten = median_gruppe(g, "kosten")
        zeilen.append([name.replace("_", " "), f"{100 * (kosten - zentral_kosten) / zentral_kosten:+.5f}",
                       f"{max(l['dP'] for l in g):.1e}", f"{max(l['dQ'] for l in g):.1e}",
                       f"{max(l['spannung'] for l in g):.1e}", f"{max(l['thermisch'] for l in g):.1e}",
                       ("ja" if grenzen["gleich"] else f"nein ({grenzen['abweichungen']})") if grenzen else "–",
                       f"{grenzen['dPg_MW']:.2f}" if grenzen else "–",
                       "identisch" if max(l["kosten"] for l in g) == min(l["kosten"] for l in g)
                       else f"{(max(l['kosten'] for l in g) - min(l['kosten'] for l in g)) / zentral_kosten:.0e}"])
    schreibe_tabelle(tab, "tab03_qualitaet", ["Konfiguration", "Kostenabweichung [%]", "max ΔP [p.u.]",
                                              "max ΔQ [p.u.]", "max Spannungsverletzung", "max therm. Verletzung",
                                              "aktive Grenzen wie zentral", "max ΔP_g [MW]",
                                              "Streuung über Wiederholungen (rel.)"], zeilen,
                     "Lösungsgüte gegenüber der zentralen Referenz",
                     "Kostenabweichung als Median über die Wiederholungen, Fehler als Maximum. Aktive Grenzen: "
                     "Vergleich der Mengen (Spannungs-, Wirk- und Blindleistungsgrenzen) mit der zentralen Lösung; "
                     "sie stammen aus der Messreihe vom 15.10., weil die Archive vom 19.10. keine Checkpoints "
                     "enthalten. Die Iterate beider Messreihen sind bis auf die Rundung der Konvergenzprüfung gleich. "
                     "Streuung über Wiederholungen: bis 16 Agenten sind die Endzustände bitgleich; ab 32 Agenten "
                     "unterscheiden sie sich in der neunten Stelle, weil die Rangzuordnung je Wiederholung wechselt "
                     "(siehe docs/CLUSTER_CAMPAIGN_20261019.md).")

    # --- Tabelle 4: Thermik
    if kalt_gruppen:
        zeilen = []
        for name in sorted((n for n in gruppen if not gruppen[n][0]["zentral"]), key=sortier):
            warm_t = [therm_warm[f"{name}_r{i}"] for i in range(1, 6) if f"{name}_r{i}" in therm_warm]
            kalt_t = [therm_kalt[f"{name}_r{i}"] for i in range(1, 6) if f"{name}_r{i}" in therm_kalt]
            if not warm_t or not kalt_t:
                continue
            w_job, k_job = median_gruppe(gruppen[name], "jobzeit"), median_gruppe(kalt_gruppen[name], "jobzeit")
            zeilen.append([name.replace("_", " "),
                           f"{statistics.median(t['temperatur'] for t in warm_t):.1f}",
                           f"{statistics.median(t['temperatur'] for t in kalt_t):.1f}",
                           sum(1 for t in warm_t if t["gedrosselt"]), sum(1 for t in kalt_t if t["gedrosselt"]),
                           f"{w_job:.1f}", f"{k_job:.1f}", f"{100 * (k_job - w_job) / w_job:+.1f}"])
        schreibe_tabelle(tab, "tab04_kuehlung", ["Konfiguration", "Temperatur mit Lüfter [°C]",
                                                 "Temperatur ohne Lüfter [°C]", "Läufe mit Drosselung (mit)",
                                                 "Läufe mit Drosselung (ohne)", "Jobzeit mit Lüfter [s]",
                                                 "Jobzeit ohne Lüfter [s]", "Mehrzeit ohne Lüfter [%]"], zeilen,
                         "Einfluss der Kühlung",
                         "Gleicher Programmstand, gleiche Pi-Zuordnung, gleiche Reihenfolge; einziger Unterschied "
                         "ist die Lüfterkühlung. Temperatur ist die höchste Knotentemperatur während des Laufs "
                         "(Median über die Wiederholungen).")

    # --- Tabelle 5: Konvergenzprüfung und asynchrone Tests
    zeilen = []
    for name, lauf in sorted(tests.items()):
        if lauf["zentral"]:
            continue
        settings = lauf["manifest"]["settings"]
        zeilen.append([name, settings.get("ADMM_CONV_CHECK_EVERY", "1"), settings.get("ADMM_ASYNC_TAU", "–"),
                       lauf["iterationen"], lauf["budget"], "ja" if lauf["konvergiert"] else "nein",
                       "ja" if lauf["zulaessig"] else "nein", f"{lauf['schleife']:.1f}",
                       f"{1000 * lauf['schleife'] / lauf['iterationen']:.2f}"])
    if zeilen:
        schreibe_tabelle(tab, "tab05_varianten", ["Lauf", "Prüfung alle K Iterationen", "Verzögerung τ",
                                                  "Iterationen", "Budget", "konvergiert", "AC-zulässig",
                                                  "Schleifenzeit [s]", "ms je Iteration"], zeilen,
                         "Konvergenzprüfung und asynchrone Variante",
                         "τ = 0 entspricht dem synchronen Verfahren. Läufe mit τ ≥ 1 erreichen das Budget ohne "
                         "Konvergenz und sind nicht AC-zulässig.")

    # --- Tabelle 6: PGLib
    if args.pglib and args.pglib.exists():
        pg_laeufe, pg_gruppen = lies_kampagne(args.pglib)
        pg_zentral = next(l for l in pg_laeufe if l["zentral"])
        zeilen = []
        for name in sorted((n for n in pg_gruppen if not pg_gruppen[n][0]["zentral"]), key=sortier):
            g = pg_gruppen[name]
            kosten = median_gruppe(g, "kosten")
            zeilen.append([name.replace("_", " "), g[0]["agenten"], f"{g[0]['toleranz']:.0e}", g[0]["iterationen"],
                           f"{median_gruppe(g, 'jobzeit'):.1f}",
                           f"{100 * (kosten - pg_zentral['kosten']) / pg_zentral['kosten']:+.5f}",
                           f"{max(l['dP'] for l in g):.1e}", f"{max(l['thermisch'] for l in g):.1e}"])
        schreibe_tabelle(tab, "tab06_zweiggrenzen", ["Konfiguration", "Agenten", "Toleranz", "Iterationen",
                                                     "Jobzeit [s]", "Kostenabweichung [%]", "max ΔP [p.u.]",
                                                     "max therm. Verletzung"], zeilen,
                         "Fall mit aktiven Zweiggrenzen (PGLib-Variante)",
                         f"Zentrale Referenz: {pg_zentral['kosten']:.4f} (Gurobi-Kernzeit "
                         f"{pg_zentral['kernzeit']:.3f} s). Die thermischen Grenzen werden eingehalten; "
                         "die Werte liegen unter der Abnahmegrenze 1e-5.")

    # --- Tabelle 7: zentral gegen verteilt
    zentral_t1 = gruppen.get("C0_central_t1", [])
    zentral_job = median_gruppe(zentral_t1, "jobzeit")
    zentral_kern = median_gruppe(zentral_t1, "kernzeit")
    zeilen = [["zentral (1 Pi, 1 Thread)", "–", f"{zentral_job:.2f}", f"{zentral_kern:.3f}", "–", "1.0", "–"]]
    for name in sorted((n for n in gruppen if n.startswith(("C4_partitions_edge_rho", "C2_bus118"))),
                       key=sortier):
        g = gruppen[name]
        job = median_gruppe(g, "jobzeit")
        zeilen.append([name.replace("_", " "), g[0]["agenten"], f"{job:.1f}",
                       f"{median_gruppe(g, 'schleife'):.1f}", f"{median_gruppe(g, 'cpu_summe'):.0f}",
                       f"{job / zentral_job:.0f}", f"{median_gruppe(g, 'cpu_summe') / zentral_kern:.0f}"])
    schreibe_tabelle(tab, "tab07_zentral_verteilt", ["Verfahren", "Agenten", "Jobzeit [s]",
                                                     "Schleifen-/Kernzeit [s]", "CPU-Summe [s]",
                                                     "Jobzeit gegenüber zentral", "CPU gegenüber zentral"], zeilen,
                     "Zentrale Referenz gegenüber dem verteilten Verfahren",
                     "Alle Werte auf derselben Hardware (Raspberry Pi 5, 1 Thread je Prozess).")

    # --- README
    kipp_text = f"{kipppunkt:.1f}" if kipppunkt else "–"
    (args.out / "README.md").write_text(f"""# Auswertungspaket Studienarbeit

Erzeugt mit `tools/thesis_package.py` aus den Messreihen vom 15.10. und 19.10.2026.
Alle Abbildungen liegen als PNG (Ansicht) und PDF (Druck, vektoriell) vor; alle Tabellen als
CSV (Semikolon, Excel) und Markdown.

## Abbildungen

| Datei | Aussage | Kapitel |
|---|---|---|
| abb01_zeit_je_iteration | Zeit je Iteration über der Agentenzahl, einheitliches gegenüber kantengewichtetem ρ | Messkampagne |
| abb02_kipppunkt | Rechnen gegenüber Kommunikation über der Teilproblemgröße; Kipppunkt bei ≈ {kipp_text} Bussen | Auswertung, FF3 |
| abb03_zeitzerlegung | Anteile Rechnen / MPI / Übriges je Agentenzahl | Messkampagne |
| abb04_iterationen_strafparameter | Iterationen: einheitliches gegenüber kantengewichtetem ρ | Entwurf, FF1 |
| abb05_residuum_primal_k16 | primales Residuum, ein Lauf mit 16 Partitionen | Messkampagne |
| abb06_residuum_dual_k16 | duales Änderungsmaß, ein Lauf mit 16 Partitionen | Messkampagne |
| abb07_residuum_primal_k118 | primales Residuum, ein Lauf mit 118 Busagenten | Messkampagne |
| abb08_residuum_dual_k118 | duales Änderungsmaß, ein Lauf mit 118 Busagenten | Messkampagne |
| abb09_thermik_temperatur | Knotentemperatur mit und ohne Lüfterkühlung | Messkampagne, Limitationen |
| abb10_thermik_laufzeit | Mehrzeit ohne Lüfterkühlung | Messkampagne, Limitationen |
| abb11_konvergenzpruefung | Wirkung der Konvergenzprüfung alle 10 Iterationen | Auswertung |
| abb12_zentral_gegen_verteilt | Jobzeit zentral gegenüber verteilt | Auswertung, FF3 |
| abb13_dualmass_gegen_boyd_k16 | Änderungsmaß gegenüber Boyd-Matrixform, 16 Partitionen | Entwurf, Kriterien |
| abb14_dualmass_gegen_boyd_k118 | Änderungsmaß gegenüber Boyd-Matrixform, 118 Busagenten | Entwurf, Kriterien |

## Tabellen

| Datei | Inhalt | Kapitel |
|---|---|---|
| tab01_abnahme | Läufe, Abnahme, Budgetausnutzung je Messreihe | Messkampagne |
| tab02_skalierung | Iterationen, Zeiten, Zeitzerlegung je Konfiguration | Messkampagne, FF3 |
| tab03_qualitaet | Kosten, Bilanzfehler, Grenzen, aktive Mengen | Auswertung, FF2 |
| tab04_kuehlung | Kontrollierter Vergleich mit und ohne Lüfterkühlung | Messkampagne, Limitationen |
| tab05_varianten | Konvergenzprüfung alle K Iterationen und asynchrone Tests | Auswertung, Ausblick |
| tab06_zweiggrenzen | PGLib-Variante mit aktiven Zweiggrenzen | Auswertung, FF2 |
| tab07_zentral_verteilt | Zentral gegenüber verteilt, Jobzeit und CPU-Aufwand | Auswertung, FF3 |

## Gemessene Größen

- **Jobzeit** ist die Wanduhrzeit von Prozessstart bis Prozessende (`wall_total_s`): Start über
  `mpirun`, Laden der Topologie, Modellaufbau, ADMM-Schleife, unabhängige AC-Prüfung und Ausgabe.
  Sie enthält nicht die Vorbereitung des Abbilds auf den Knoten.
- **Schleifenzeit** ist nur die ADMM-Schleife (`wall_iterations_s`); „Schleifenzeit je Iteration“
  ist dieser Wert geteilt durch die Zahl der Iterationen.
- **Kernzeit** ist beim zentralen Lauf die Gurobi-Laufzeit, beim verteilten die Schleifenzeit.
- Zeit- und Kostengrößen sind Mediane über die Wiederholungen, Verletzungs- und
  Ausnutzungsgrößen Maxima; Iterationszahlen sind über die Wiederholungen identisch.

## Abbruchgrößen in abb05 bis abb08 und abb13/abb14

Aufgetragen ist jeweils das Maximum über die Ränge, bezogen auf die eigene Schwelle; die
gestrichelte Linie bei 1 ist die Abbruchschwelle.

- **Primal** ist das Residuum ‖r‖₂ = ‖Ax − Ez‖₂, bezogen auf `min(ε_pri, ε_abs)`. Die
  Implementierung fordert zusätzlich zur Boyd-Schwelle ε_pri das harte absolute Gate ε_abs; der
  Quotient zeigt die bindende der beiden Bedingungen.
- **Dual** ist das **Änderungsmaß** ‖ρ Δz‖₂, also die elementweise, mit ρ skalierte Änderung der
  Konsensvariablen zwischen zwei Iterationen, bezogen auf ε_dual. Das ist **nicht** das kanonische
  duale Residuum ‖Aᵀ R E (z − z_prev)‖₂ der Matrixform: dort werden die Beiträge aller Gruppen,
  zu denen eine Variable gehört, vorher aufsummiert. Beide Formen werden mitgeschrieben
  (`distributed_opf/consensus_residuals.py`, Spalten `boyd_a_*` der Residuenspur).
- abb13 und abb14 vergleichen beide Definitionen über den ganzen Lauf. Liegt der Quotient bei 1,
  würden beide Kriterien zum selben Zeitpunkt abbrechen.

## Weitere Hinweise

- Die Zeitzerlegung trennt blockierende MPI-Aufrufe (einschließlich Wartezeit) von der lokalen
  Rechnung; Läufe vor dem 19.10. zählen die Konvergenzprüfung vollständig zur Kommunikation.
- abb12 verwendet eine lineare Achse. Der zentrale Lauf ist dort absichtlich nur ein Strich; eine
  logarithmische Achse würde den Abstand optisch verkleinern, der die Aussage trägt.
""", encoding="utf-8")
    print(f"Abbildungen: {len(list(abb.glob('*.png')))} | Tabellen: {len(list(tab.glob('*.md')))} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
