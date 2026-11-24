# Messprotokoll: zentral gegen verteilt (AC-OPF, IEEE118)

Gilt für die lokale Vorserie und unverändert für den Cluster. Abweichungen werden
im Bericht einzeln ausgewiesen.

## 1. Gegenstand und Qualität

- Fall: `topology_ieee118_full.yaml` (SHA256 im Manifest), vollständige nichtkonvexe
  AC-Gleichungen, keine PWL-Näherung, keine Nachbearbeitung.
- Zentral: dieselbe Formulierung, Gurobi lokal (`OptimalityTarget=1`).
- Verteilt: rohes ADMM, festes einheitliches ρ=3e5 (Abstimmungsprotokoll T11), zentrierte Strafdarstellung,
  α=1, Partitionierer `connected_impedance`, k ∈ {3, 4, 8, 16}.
- Ein Lauf zählt nur, wenn er **akzeptiert** ist: zentral Solverstatus 2/18 und
  unabhängige AC-Prüfung; verteilt strikte primale und duale Konvergenz auf allen
  Rängen, alle lokalen Solves Status 18, unabhängige AC-Prüfung
  (Bilanz 1e-4 p.u., Grenzen 1e-5). Keine Toleranzlockerung.
- Qualitätsvergleich über physische Erzeugungskosten gegen die zentrale Referenz
  sowie maximale P-/Q-Fehler. Kein globales Optimalitätszertifikat.
- 118 busweise Agenten sind mit einheitlichem ρ nicht konvergent (Phase 2) und
  gehören nicht zur Messreihe.

## 2. Ablauf

- Nur frische, ununterbrochene Läufe, keine Fortsetzungen. Lokal mit `--scratch`
  (Laufverzeichnis außerhalb des Windows-Dateisystems), auf dem Cluster ohne `--scratch`, weil
  alle Ränge dasselbe NFS-Verzeichnis brauchen (`docs/CLUSTER_RUNBOOK.md` §5).
- Iterationsbudget je Konfiguration aus `cluster/case_studies.yaml` (1.25 × lokal gemessen);
  ein Lauf, der das Budget erreicht, gilt als nicht konvergiert und wird so berichtet.
- Kampagne: `--log-every 0 --monitor-every 0 --timing-trace --checkpoint-every 500`
  (`cluster/case_studies.yaml`). Die Checkpoints erlauben die Auswertung der Toleranzstufen und
  Qualitätsmeilensteine; ihr Schreiben wird als `io` getrennt gemessen. Nur für reine
  Zeitmessungen ohne diese Auswertung `--checkpoint-every 0` verwenden und das im Bericht
  ausweisen. Endcheckpoint, abschließende AC-Prüfung und Zusammenfassung laufen immer.
- Gurobi-Threads je Prozess: 1 (`--threads 1`) für zentral und jeden Agenten;
  zusätzlich zentral mit allen Threads (`--threads 0`) als obere Referenz.
- `--batch` gleich der Agentenzahl (alle Ränge lösen gleichzeitig).
- Wiederholungen: verteilt 3 je k, zentral 5 je Thread-Einstellung je Block;
  Reihenfolge der k je Wiederholung rotiert, zentrale Läufe vor jedem Block.
- Lokal: kein anderer rechenintensiver Prozess; `--mpi-yield` nur bei Überbelegung
  (hier nicht: höchstens 16 Ränge auf 16 logischen CPUs).

## 3. Messgrößen

| Größe | Quelle | Bedeutung |
|---|---|---|
| Jobzeit | `result.json: wall_total_s` | Prozessstart bis -ende inkl. Python-Start, Modellaufbau, Lösen, Prüfung |
| zentral: Laden / Aufbau / Optimierung / Gurobi | `central.timing` | Zerlegung der zentralen Jobzeit |
| Schleifenzeit | `wall_iterations_s` | nur ADMM-Iterationen (max. über Ränge) |
| ms je Iteration | Schleifenzeit / Iterationen | |
| x-Schritt je Rang | `phases.x_solve` | lokales Lösen; Summe, Maximum, Minimum über Ränge |
| Warten/Austausch | `phases.consensus_exchange + solve_status` | enthält Warten auf langsamere Ränge, keine reine Netzzeit |
| Kommunikation (MPI inkl. Warten) | `vt_exchange + solve_status + consensus_exchange + convergence_mpi` | alle blockierenden MPI-Aufrufe der Iteration |
| lokale ADMM-Rechnung | `zu_update + convergence` | z-/u-Schritt, Residuen und Toleranzen (ohne MPI und Ausgabe) |
| Ausgabe/Beobachtung | `io + console + monitor` | Logs, Checkpoints, Konsolenzeile, AC-Beobachtung |
| Overhead | Schleifenzeit × Ränge − alle Phasen | nicht zugeordnete Zeit |
| CPU-Summe | `cpu_rank_sum_s` | Prozess-CPU aller Ränge (inkl. aktivem MPI-Warten) |
| Solverzeit-Summe | `solver.optimize_wall_s` | Summe aller Gurobi-Aufrufe |
| MPI-Aufrufe / Pufferbytes | `rank_metrics.mpi` | Nutzdatenpuffer, kein gemessener Netzwerkverkehr |

Kommunikationsstruktur: Der Konsensaustausch erfolgt nur mit Nachbarrängen
(`Neighbor_alltoallv`). Je Iteration kommen globale Reduktionen hinzu: zwei für die
Abbruchprüfung (`convergence_mpi`) und im busweisen Modus eine für den Solverstatus
(`solve_status`). „Nur Nachbarkommunikation“ gilt daher für den Konsensaustausch, nicht für den
gesamten Algorithmus. Läufe vor der Aufteilung (Kennzeichnung `timing_model = legacy`) enthalten in
`convergence` zusätzlich Residuenrechnung und Konsolenausgabe; ihre Kommunikationsanteile sind
obere Schranken und werden so gekennzeichnet.

**Trennung der Messreihen (verbindlich):** Je Softwarestand und je Rechnerumgebung ein eigenes
Kampagnenverzeichnis. Verzeichnisse verschiedener Stände oder Umgebungen werden nicht gemeinsam zu
Wiederholungen aggregiert. `tools/aggregate_benchmarks.py` erzwingt das, indem Quellcode-Hashes
aller Kernmodule und die Plattform Teil des Konfigurationsschlüssels sind; Zeiten aus
unterschiedlichen Umgebungen erscheinen damit nie als Wiederholungen derselben Konfiguration.
Reine Laufzeiteinstellungen (z. B. Checkpoint-Intervall) ändern die Iterate nicht, wohl aber die
Zeiten: Sie sind im Schlüssel nicht enthalten und deshalb je Serie konstant zu halten.

Auswertung je Konfiguration: Median sowie Minimum/Maximum über Wiederholungen.
Endzustände wiederholter Läufe müssen bitgleich sein (Determinismusprüfung).
Werkzeug: `tools/aggregate_benchmarks.py <ordner> --out <ordner>/aggregate`.

## 4. Interpretation

- Speedup ist eine offene Frage und wird nur aus akzeptierten Läufen gleicher
  Qualität berichtet, getrennt nach Jobzeit und Kernzeit (Optimierung bzw. Schleife).
- Zentral und verteilt auf **derselben Hardwareklasse** vergleichen; lokale Zeiten
  (WSL, 16 logische CPUs) sind nicht auf die Pis übertragbar.
- Zusätzlich berichten: Iterationen, CPU-Aufwand, Wartezeitanteil, Größe der
  größten Partition (bestimmt den langsamsten Rang).
- Zwei Vergleiche werden getrennt ausgewertet:
  1. **Hardwareeffekt:** dieselbe ADMM-Konfiguration mit derselben Partitionierung lokal gegen
     Cluster (z. B. C1 k=8 lokal aus `validation/local_campaign_tol1e4_20260915/` gegen C1 k=8
     auf den Pis). Unterschiede stammen dann aus Hardware, Netzwerk und Gleitkomma.
  2. **Gesamtleistung:** verteilte Konfigurationen gegen das zentrale Verfahren auf derselben
     Hardwareklasse (C0 gegen C1/C2).
- Die Serie C1 (3/4/8/16) ist **kein isolierter Skalierungstest**: Mit der Agentenzahl ändern
  sich auch Partitionierung, Schnittkanten und damit Iterationszahl. Aussagen zur Skalierung
  werden deshalb je Iteration (ms je Iteration, Zeitzerlegung, Teilproblemgröße) getroffen, nicht
  über die Gesamtlaufzeit allein.
