# Abstimmungsprotokoll

Alle Entscheidungen über Verfahren und Parameter wurden am selben Testfall (IEEE 118, lokal)
getroffen. Dieses Protokoll legt offen, **welche Entscheidung auf welchen Läufen beruht**, damit
die Messkampagne als eingefrorene, vorab festgelegte Konfiguration gelten kann und keine
nachträgliche Anpassung an Clusterergebnisse erfolgt. Chronologisch; Datum = Entscheidung.

| # | Datum | Entscheidung | Grundlage (Läufe) | Kriterium | Alternativen und Ergebnis |
|---|---|---|---|---|---|
| T1 | 11.09. | Vollständiges nichtlineares AC-Modell, keine PWL-Näherung, keine NR-Nachbearbeitung | Aufgabenstellung, `docs/FULL_AC_MODEL.md` | physikalische Zulässigkeit ohne Nachbearbeitung | PWL/McCormick und Polish verworfen (`legacy/`) |
| T2 | 11.09. | Kopplung über V und θ an Schnittkanten (Kopien), nicht über P/Q | Modellherleitung, `docs/FORMULATION.md` §2 | exakte lokale Bilanzen inkl. Verlusten | – |
| T3 | 14.09. | Zentrierte Strafdarstellung | `validation/centered_118_20260914_initial/` | numerische Stabilität bei großem ρ, algebraisch identisch | direkte Form: lokale Abbrüche bei ρ=1e8 |
| T4 | 14.09. | Strikte Toleranz 1e-5 (abs und rel) je Rang als Referenzkriterium | Vorgabe | konservativ | – |
| T5 | 14.09. | Einheitliches ρ für 118 Agenten nicht tragfähig | `validation/phase2_pilots_20260914/`, `phase2b_variants_20260914/` | duale Konvergenz in 3000 Iterationen | ρ 1e6…1e8, getrenntes ρ_V/ρ_θ, adaptiv, α=1.6: alle nicht konvergent |
| T6 | 14.09. | Partitionierer `connected_impedance` | `validation/phase5_partitions_20260914/` gegen `full_rho1e6_transfer_20260914/` | Zusammenhang, geschnittene Admittanz, Konvergenz | `graph_aware`: 4/8/16 unzulässig |
| T7 | 15.09. | Kantengewichtetes ρ als getrennte Variante für 118 Agenten, p=1, ρ₀=1e6 | `validation/edge_rho_20260915/` (Piloten 3000 Iterationen) | kleinster dualer Quotient bei 3000 | p=1: ρ₀ 3e5 instabil, 3e6 / 1e7 langsamer; p=0.5, ρ₀=3e6 langsamer |
| T8 | 15.09. | Kampagnentoleranz 1e-4 (vorläufig) | `validation/iteration_budget_20260915/` (Checkpoints der ρ=1e6-Läufe) | lockerste Stufe, bei der alle Konfigurationen zulässig sind | 1e-3: 16 und 118 Agenten unzulässig |
| T9 | 15.09. | Iterationsbudgets = 1.25 × lokal gemessene Iterationen | T8 | Reserve für Plattformeffekte | – |
| T10 | 15.09. | Flacher Start in der Kampagne | `results/init_study_20260915/`, `validation/central_multistart_20260915/` | im verteilten Betrieb verfügbar; Optimum startunabhängig | zentraler Start: 3–10× weniger Iterationen, nicht verfügbar |
| T11 | **entschieden 15.09.2026: ρ = 3e5** | **ρ für Partitionen** | ρ-Serie 1e5 / 3e5 / 1e6 / 3e6 (`results/rho_sensitivity_20260915/`, `benchmark_local_20260914/`) | Iterationen bis strikte Konvergenz, Zulässigkeit, Einklammerung | 3e5: 936 / 3025 / 2955 / 5834 (alle akzeptiert; einziger Wert der Serie, der bei allen vier Partitionierungen konvergiert, daher robuster gemeinsamer Wert; bei 3 Partitionen ist 1e5 mit 465 schneller); 1e6: 3124 / 10013 / 9917 / 19374; 1e5: 465 / n.k. / n.k. / n.k. (unzulässig); 3e6: 9369 / n.k. / n.k. / n.k. (Budget 25000). Optimum beidseitig eingeklammert. Budgets C1 1200/2500/2500/5000, C3 1200/3800/3700/7300 (1.25 × Checkpoint-Schranke bzw. strikte Iterationen) |
| T12 | **entschieden 15.09.2026: 1e-4** (C3 misst 1e-5 zusätzlich) | Kampagnentoleranz endgültig (1e-4 oder 1e-5 für Partitionen) | T11 + lokaler Kampagnentest `results/local_campaign_tol1e4_20260915/` | Aufwand bei zulässigem Zustand | 1e-4 bei ρ=3e5: 633 / 1723 / 1746 / 3185, alle akzeptiert, Kosten ≤ 0.001 %; 118 kantengewichtet 15219, akzeptiert, +0.059 %; C3 misst 1e-5 zusätzlich |

Gegenprobe nach dem Einfrieren (keine Änderung): PGLib-Variante mit ρ=3e5, 1e-4 und
1e-5, alle akzeptiert, kleine Reserve bei 1e-4 (`docs/PGLIB_HOLDOUT.md`).

## Einfrieren

Nach T11/T12: `cluster/case_studies.yaml` wird mit den gewählten Werten committet, der Commit
getaggt (`lokal-final`) und im Bericht als Kampagnenkonfiguration zitiert. Änderungen nach dem
Einfrieren nur mit neuem Protokolleintrag und Begründung, nie auf Basis von Clusterergebnissen.

## Nicht abgestimmte Größen (fest)

Bilanztoleranz 1e-4 p.u. und Grenztoleranz 1e-5 der unabhängigen AC-Prüfung; lokale
Solver-Toleranzen (NLBar 1e-9, Feasibility 1e-8); 1 Gurobi-Thread je Agent; Topologie und
Kostenkoeffizienten des Quelldatensatzes.
