# Phase 1: Messfähige Implementierung (14. September 2026)

Ziel: Laufzeiten von zentralem und verteiltem AC-OPF auf realer Hardware fair
messbar machen, **ohne die Mathematik zu verändern**. Modell, ADMM-Schritte,
Rho, Toleranzen, Abbruchkriterien und die unabhängige AC-Prüfung sind unverändert.
Kein kantengewichtetes Rho, keine PWL-Näherung, keine Nachbearbeitung.

## Anlass (Befund aus den 118-Agenten-Läufen vor Phase 1)

Im Lauf `full_rho1e6_transfer_20260914/partition_118` (4000 Iterationen) entfielen
pro Rang etwa 16 s auf Gurobi, aber 289 s auf blockierende MPI-Aufrufe.
Ursachen im Code: globale JSON-`Allgatherv`/`Allgather` an alle Ränge, obwohl nur
1–9 Nachbarn die Daten brauchen; zwei `Barrier` je Iteration; drei globale
Reduktionen für die Konvergenzprüfung plus eine Pickle-Reduktion; globale
AC-Prüfung alle 20 Iterationen innerhalb der Schleife; JSONL-Log jedes Busses in
jeder Iteration (~300 MB je Lauf). Lokal kommen 118 Ränge auf 16 logischen CPUs
hinzu. Diese Zeiten messen daher überwiegend die Implementierung, nicht das Verfahren.

## Änderungen

| Bereich | Änderung | Datei |
|---|---|---|
| Konsensaustausch | Nur Nachbarränge über einen MPI-Nachbarschaftsgraphen (`Neighbor_alltoallv`), 8 float64 je Schnittkante statt JSON an alle | `distributed_opf/neighbor_exchange.py`, `agent_ac_mpi.py` |
| Kantenreihenfolge | Beide Endpunkte sortieren eine Kante nach (Bus auf kleinerem Rang, Bus auf größerem Rang); beim Start wird die Übereinstimmung aktiv geprüft | `neighbor_exchange.py` |
| V/θ vor dem x-Schritt | Aus dem letzten Konsensaustausch übernommen (Werte sind bis zum nächsten x-Schritt unverändert); nur beim Start, nach Checkpoint oder Zustandswiederherstellung explizit getauscht | `agent_ac_mpi.py` |
| Synchronisation | `Barrier` am Iterationsende entfernt; Konvergenz mit zwei statt drei Reduktionen; Solverstatus als Integer-Reduktion statt Pickle | `agent_ac_mpi.py` |
| Phasenzeiten | Je Rang und Iteration: `vt_exchange`, `x_solve`, `solve_status`, `consensus_exchange`, `zu_update`, `convergence`, `monitor`, `io`; Summen und Quantile in `rank_metrics.json`, optional vollständige Reihen (`--timing-trace`, Spalten in dieser Reihenfolge). `solve_status` ist im Busmodus die globale Statusreduktion nach dem x-Schritt, also das Warten auf die langsamste lokale Lösung | `agent_ac_mpi.py` |
| Schleifenzeit | Neues Feld `wall_iterations_s` (nur Iterationsschleife, ohne abschließende Prüfung, Checkpoint und Zusammenfassung) | `agent_ac_mpi.py` |
| Beobachtung getrennt | `--monitor-every` (bisher fest 20), `--log-every` (bisher 1), `--checkpoint-every` (bisher 500); Defaults entsprechen dem bisherigen Verhalten | `tools/run_ac_local.py` |
| Warmstart im Checkpoint | Vollständiger Solver-Startvektor und bei Partitionen die letzte lokale Lösung werden gespeichert und wiederhergestellt | `agent_ac_mpi.py`, `admm_ac.py` |
| Lokale Überbelegung | `--mpi-yield` (Open MPI `mpi_yield_when_idle`), ändert keine Iterate | `tools/run_ac_local.py` |

Die Konvergenzentscheidung, der physikalische Gate-Check vor einer Erfolgsmeldung
und die abschließende unabhängige AC-Prüfung laufen weiterhin immer.
`--monitor-every 0` entfernt nur die zusätzlichen Zwischenprüfungen.

## Nachweis 1: bitgleiche Iterate alter gegen neuer Code

Fünf Fälle mit identischen Einstellungen, alter Stand `0d995e4` (separater
Worktree) gegen neuen Code. Verglichen wurden **alle** Agent-Logeinträge
(V, θ, Pg, Qg, Flüsse, Residuen, Kosten) sowie die Endcheckpoints mit sämtlichen
Zuständen, Kopien, z- und u-Werten, jeweils exakt (`tools/compare_admm_runs.py`).

| Fall | Einstellung | Logeinträge | Ergebnis beider Stände | identisch |
|---|---|---:|---|---|
| 3 Busse, 3 Agenten | Rho 1000, centered, bis 300 | 882 | konvergiert bei 293, AC-zulässig | ja |
| 3 Busse, 2 Partitionen | Rho 1000, centered, bis 250 | 600 | konvergiert bei 199, AC-zulässig | ja |
| IEEE118, 118 Busagenten | Rho 1e8, centered, 40 | 4720 | 40 Iterationen, nicht konvergiert | ja |
| IEEE118, 3 Partitionen | Rho 1e6, centered, 40 | 4720 | 40 Iterationen, nicht konvergiert | ja |
| IEEE118, 8 Partitionen | Rho 1e6, centered, 40 | 4720 | 40 Iterationen, nicht konvergiert | ja |

Nachweise: `validation/phase1_equivalence_20260914/equivalence/`
(Vergleichsberichte, Manifeste mit Quell-Hashes, Rangmetriken beider Stände).

Kommunikation in diesen 40-Iterationen-Läufen (lokal, WSL, 16 logische CPUs;
nur indikativ, kein Benchmark):

| Fall | MPI-Aufrufe je Rang alt → neu | Pufferbytes je Rang alt → neu | Schleifenzeit alt → neu |
|---|---:|---:|---:|
| 118 Busagenten | 332 → 175 | 48.8 kB → 8.0 kB | 3.81 s → 1.73 s |
| 3 Partitionen | 334 → 137 | 1561 kB → 48.7 kB | 1.73 s → 1.59 s |
| 8 Partitionen | 339 → 142 | 602 kB → 35.9 kB | 1.17 s → 1.02 s |

Pufferbytes sind Nutzdatenpuffer, kein gemessener Netzwerkverkehr. MPI-Zeiten
enthalten Warten auf andere Ränge.

## Nachweis 2: Fortsetzung und Benchmark-Modus

Alle Läufe mit dem endgültigen Code, exakter Vergleich wie oben.

| Vergleich | Einträge | identisch |
|---|---:|---|
| IEEE118, 3 Partitionen: 40 durchgehend gegen 20 + Fortsetzung bis 40 | 2360 | ja |
| IEEE118, 118 Busagenten: 40 durchgehend gegen 20 + Fortsetzung bis 40 | 2360 | ja |
| 3 Busse, 2 Partitionen: 120 durchgehend gegen 60 + Fortsetzung bis 120 | 180 | ja |
| 118 Busagenten: volles Logging gegen Benchmark-Modus (`--log-every 0 --monitor-every 0 --checkpoint-every 0 --timing-trace --mpi-yield`) | 118 + Endcheckpoint | ja |
| 3 Partitionen: volles Logging gegen Benchmark-Modus | 118 + Endcheckpoint | ja |
| **Kontrolle alter Code**: 3 Partitionen, 20 + Fortsetzung gegen durchgehend | 2360 | **nein**, Abweichung ab Iteration 21 |

Der alte Stand verlor beim Fortsetzen den Solver-Startvektor; die nichtkonvexen
lokalen Probleme landeten dadurch in minimal anderen Punkten (Zustände ~1e-8,
duales Residuum ~2e-4 in Iteration 21). Das betrifft den bisherigen
Drei-Partitionen-Erfolg, der aus zwei Abschnitten besteht: Er ist ein gültiger
strikter Lauf, aber nicht bitgleich zu einem ununterbrochenen Lauf. Eine frische
Wiederholung ohne Neustart bleibt deshalb nötig.

Phasenzeiten je Rang, 40 Iterationen, lokal überbelegt (nur zur Plausibilität):

| Lauf | x_solve | solve_status | consensus_exchange | convergence | monitor | io | Schleife |
|---|---:|---:|---:|---:|---:|---:|---:|
| 118 Busagenten, volles Logging | 0.274 s | 0.900 s | 0.089 s | 0.667 s | 0.027 s | 0.013 s | 2.00 s |
| 118 Busagenten, Benchmark-Modus | 0.261 s | 0.925 s | 0.057 s | 0.631 s | 0.000 s | 0.001 s | 1.91 s |
| 3 Partitionen, Benchmark-Modus | 1.300 s | 0 | 0.117 s | 0.008 s | 0.000 s | 0.000 s | 1.43 s |

Bei 118 Busagenten auf 16 logischen CPUs dominiert Warten auf den langsamsten
Rang (`solve_status`, `convergence`), nicht das Rechnen. Das ist ein Effekt der
Überbelegung und genau die Größe, die auf dem Cluster gemessen werden muss.

Nachweise: `validation/phase1_equivalence_20260914/resume/` und `summary.json`.

## Reproduktion (WSL)

```bash
PY=/home/user/miniconda3/envs/distributed_opf/bin/python
RUN_GUROBI_TESTS=1 $PY -m unittest discover -s tests
mpirun --oversubscribe -np 4 $PY tests/mpi_neighbor_probe.py
# Benchmark-Modus: keine Zwischenprüfung, Logs nur am Ende, vollständige Phasenzeiten
$PY tools/run_ac_local.py --agents 118 --batch 118 --rho 1e8 --centered-penalty --solve-limit 0 \
    --iterations 4000 --log-every 0 --monitor-every 0 --checkpoint-every 0 --timing-trace \
    --scratch --out results/<neu>
$PY tools/compare_admm_runs.py results/<lauf_a> results/<lauf_b>
```

`--batch` muss für Laufzeitmessungen der Rangzahl entsprechen, sonst lösen die
Ränge nacheinander. `--mpi-yield` nur für überbelegte lokale Läufe verwenden
und im Bericht angeben.

## Offene Punkte nach Phase 1

- Pro Iteration bleiben drei kleine globale Reduktionen (Solverstatus, Konvergenz).
  Sie sind für die strikte Erfolgsdefinition nötig und werden als `convergence`
  bzw. im `x_solve` getrennt ausgewiesen.
- Lokale 118-Rang-Zeiten bleiben wegen Überbelegung nicht aussagekräftig;
  belastbare Zeiten erst auf dem Cluster.
- Die globale Boyd-Norm wird noch nicht zusätzlich protokolliert (Phase 0).
- Die bisherigen Konvergenzergebnisse (Phase 2: 118 zur Konvergenz bringen) sind
  von dieser Phase unberührt, da die Iterate bitgleich sind.
