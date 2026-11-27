# Nachweisindex

Welcher Ordner belegt welche Aussage. Alle Ordner enthalten kompakte, versionierte Nachweise
(Manifeste mit Quell- und Topologie-Hashes, Endzustände, Zusammenfassungen, Vergleichsberichte).
Vollständige Rohläufe liegen lokal unter `results/` gleichen Namens.

## Modell, Referenzen, Implementierung

| Aussage | Ordner | Dokument |
|---|---|---|
| Zentrale Referenz des vollständigen Falls, PYPOWER-Bestätigung | `full_ac_20260911_01/`, `full_ac_20260911_02/` | `docs/FULL_AC_MODEL.md` |
| PYPOWER/PIPS dreifach wiederholt, akzeptiert | `pypower_reference_20260915/` | `docs/FULL_AC_MODEL.md` |
| Zentrale Lösung aus 16 Startpunkten: gleiches lokales Optimum | `central_multistart_20260915/` | `docs/LIMITATIONS.md` |
| SOCP-Relaxation: numerisch bestimmte Relaxationslücke 0.248 % (Original), 0.904 % (PGLib), kein Zertifikat | `socp_lower_bound_20260915/` | `docs/FORMULATION.md` §9 |
| Nachbarkommunikation, Zeitmessung, Warmstart: Iterate bitgleich zu `0d995e4`; Fortsetzung bitgleich | `phase1_equivalence_20260914/` | `docs/PHASE1_MEASUREMENT.md` |
| Relaxations-u-Schritt korrigiert, adaptives ρ; Standard bleibt bitgleich | `phase2b_variants_20260914/equivalence/` | `docs/PHASE2_RESULTS.md` |
| Aufräumen, Residuenverlauf: Standard und Kantenvariante (p=0) bitgleich | `cleanup_gate_20260915/`, `trace_gate_20260915/` | `README.md` |
| Abschluss (Docstrings, Legacy-Kennzeichnung): 43 Tests, 10 Referenzfälle bitgleich | `final_gate_20260915/` | `docs/TODO_LOCAL.md` (R3/R4) |

## Verteilte Konvergenz

| Aussage | Ordner | Dokument |
|---|---|---|
| Frühere Partitionierung: 3 konvergiert, 4/8/16/118 nicht | `full_partitions_20260914/`, `full_rho1e6_transfer_20260914/`, `full_p3_*_20260914/` | `docs/HIGH_RHO_RESULTS.md` |
| 118 Agenten, einheitliches ρ (fest, getrennt, adaptiv, relaxiert) nicht konvergent | `highrho_unlimited_20260911_01/`, `centered_118_20260914_*`, `phase2_pilots_20260914/`, `phase2b_variants_20260914/` | `docs/PHASE2_RESULTS.md` |
| `connected_impedance`: 3/4/8/16 Partitionen strikt konvergiert | `phase5_partitions_20260914/` | `docs/PHASE5_RESULTS.md` |
| 118 Agenten, kantengewichtetes ρ: Gates, Piloten, strikte Konvergenz nach 61187; frische Wiederholung bitgleich | `edge_rho_20260915/`, `edge_rho_20260915/strict_repeat/`, `edge_rho_20260915/robustness_tol1e4/` | `docs/EDGE_RHO_RESULTS.md` |

## Messungen und Auswertung

| Aussage | Ordner | Dokument |
|---|---|---|
| Lokale Messreihe: 72 Läufe akzeptiert, Wiederholungen bitgleich, Zeiten | `benchmark_local_20260914/` | `docs/BENCHMARK_LOCAL_RESULTS.md` |
| Aktive Grenzen verteilt = zentral; Abweichung des Arbeitspunkts | `limits_utilization_20260915/` | `docs/TODO_LOCAL.md` (D2) |
| Iterationsbudget, Qualitätsmeilensteine, Toleranzstufen, Wahl 1e-4 | `iteration_budget_20260915/` | `docs/CLUSTER_RUNBOOK.md` §5a |
| Solverzeit lokaler Teilprobleme nach Größe | `subproblem_benchmark_20260915_local/` | `docs/SUBPROBLEM_SIZE.md` |
| Zweiggrenzen (PGLib-Variante): zentral, 3 und 8 Partitionen, gleiche aktive Grenzen inkl. thermischer | `pglib_limits_20260915/` | `docs/PGLIB_LIMITS.md` |
| Initialisierung und ρ-Sensitivität (Optimum 3e5 beidseitig eingeklammert) | `results/init_study_20260915/`, `rho_sensitivity_20260915/` | `docs/INIT_RHO_STUDY.md` |
| DC-OPF gegenüber AC-OPF (Kosten −2.9 % / −4.2 %, andere Einsatzentscheidung) | `dc_comparison_20260915/` | `docs/DC_COMPARISON.md` |
| Lokaler Kampagnentest ρ=3e5 / Toleranz 1e-4: alle 5 Läufe akzeptiert, im Budget | `local_campaign_tol1e4_20260915/` | `docs/TOLERANCE_BUDGET.md` |
| Gegenprobe eingefrorene Konfiguration an PGLib: alle akzeptiert, kleine Reserve bei 1e-4; Speicher 85–89 MB je Rang | `pglib_holdout_20260915/` | `docs/PGLIB_HOLDOUT.md` |
| Abbruchkriterium: Löser je Rang gegen Boyd-Matrixform (je Rang ≤ 1.1 % Abweichung, global früher) | `criterion_check_20260915/` | `docs/CRITERION_COMPARISON.md` |
| Wiederholungsprüfung nach Review: 50 Tests, 10 Referenzfälle bitgleich | `review_gate_20260915/` | `docs/FORMULATION.md` §4 |
| Clusterkampagne 15.10.2026: 80 Läufe akzeptiert, Iterationen wie lokal, Zeitzerlegung, Kipppunkt 4–5 Busse, Thermik | `cluster_campaign_20261015/` | `docs/CLUSTER_CAMPAIGN_RESULTS.md` |
| Messreihen 19.10.2026: Kühlung (kontrolliert), Konvergenzprüfung alle 10 Iterationen, PGLib auf dem Cluster, asynchroner Vorversuch | `cluster_campaign_20261019/` | `docs/CLUSTER_CAMPAIGN_20261019.md` |
| Betriebssystem der Knoten (99 Debian 12, 19 Debian 13) ohne systematischen Einfluss auf die Lösezeit, Knotenstreuung bis Faktor 1.8 | `cluster_campaign_20261015/preflight_k118.json`, `cluster_campaign_20261019/tabellen/tab08_knoten_betriebssystem.md` | `docs/LIMITATIONS.md` (17) |
| Abstimmungsentscheidungen | – | `docs/TUNING_PROTOCOL.md` |

## Noch einzutragen (Läufe in Arbeit)

- Kompakte Nachweise Initialisierungsstudie und ρ-Sensitivität, lokale Kampagne bei
  Toleranz 1e-4, frische Wiederholung 118 Agenten, Robustheit Kantenvariante.
