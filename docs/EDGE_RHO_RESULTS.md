# Kantengewichtetes ρ: knotenscharfe Verteilung auf 118 Agenten

Stand 15.09.2026. Vollständiges IEEE118-AC-OPF (`topology_ieee118_full.yaml`), rohes ADMM,
zentrierte Strafdarstellung, α=1, keine Nachbearbeitung, strikte Toleranz 1e-5, unabhängige
AC-Prüfung. Nachweise: `validation/edge_rho_20260915/`.

## Motivation

Mit einheitlichem ρ konvergieren 118 Busagenten nicht (`docs/PHASE2_RESULTS.md`): kleines ρ
pendelt, großes ρ bremst die duale Konvergenz. Ursache ist die Spreizung der Zweigsteifigkeit:
eine Konsensabweichung Δθ an Zweig e erzeugt einen Bilanzfehler von etwa |y_e|·Δθ, und |y_e|
reicht im Fall von etwa 1.3 bis 246 p.u. Ein einheitliches ρ ist damit für schwache Zweige zu
groß und für steife zu klein. Die Befunde der Partitionierung (Phase 5) stützen das: Partitionen
konvergieren, sobald steife Zweige nicht geschnitten werden.

## Variante (eigenes, ausgewiesenes Modell)

`--edge-rho admittance`, `distributed_opf/edge_penalty.py`:

    ρ_e = ρ₀ · (|y_e| / Median |y|)^p

je Konsenskante (Parallelzweige addieren ihre Admittanz), gleich für die V- und θ-Gruppen
der Kante. Beide Kopien einer Konsensgruppe teilen ρ_e, daher bleibt der z-Schritt der einfache
Mittelwert; x-Schritt-Strafe, duales Residuum ρ_e·Δz und die duale Toleranz verwenden ρ_e.
Das ist ADMM mit fester diagonaler Strafmatrix. Faktoren bei p=1: 0.21 bis 21.3.

## Absicherung

| Gate | Ergebnis |
|---|---|
| Standardpfad (ohne Variante) gegen Phase 1, fünf Referenzfälle | bitgleich |
| Variante mit p=0 (alle Faktoren exakt 1.0) gegen Standard, fünf Referenzfälle | bitgleich |
| Unit-Tests (Symmetrie, Normierung, Klammerung, duales Residuum und Toleranz mit ρ_e) | bestanden |

Nachweise: `validation/edge_rho_20260915/gates/`, `validation/cleanup_gate_20260915/`,
`validation/trace_gate_20260915/`.

## Piloten, 118 Busagenten, je 3000 Iterationen

| Einstellung | Primal max | Dual max / Median | max. P-Fehler | Kosten |
|---|---:|---:|---:|---:|
| einheitlich ρ=1e7 (bestes einheitliches ρ, Phase 2) | 0.88 | 415 / 44 | 5.0e-4 | +1.30 % |
| **p=1, ρ₀=1e6** | 0.40 | **48 / 22** | 5.3e-4 | +0.33 % |
| p=1, ρ₀=3e6 | 0.10 | 107 / 34 | 1.0e-4 | +0.89 % |
| p=1, ρ₀=1e7 | 0.41 | 527 / 87 | 1.9e-4 | +1.50 % |
| p=0.5, ρ₀=3e6 | 0.16 | 161 / 34 | 4.9e-4 | +0.70 % |
| p=1, ρ₀=3e5 | 450 | 2752 / 11 | 0.14 | unzulässig |

Auswahl p=1, ρ₀=1e6: kleinster dualer Quotient; ρ₀=3e5 wird instabil.

## Konvergenz

| Abschnitt | Iterationen | Befund |
|---|---:|---|
| frischer Lauf | 1–30000 | ab ~10000 durchgehend AC-zulässig; Budget erreicht |
| Fortsetzung (bitgleich, Phase 1) | 30001–61187 | **strikt konvergiert auf allen 118 Rängen** |
| frische Wiederholung ohne Unterbrechung (15.09.) | 1–61187 | **erneut strikt konvergiert bei 61187, bitgleich** |

Endzustand bei Iteration 61187: unabhängig AC-zulässig, max. P-/Q-Fehler 2.3e-6 / 6.1e-6 p.u.,
Kosten 129661.5409 (+0.00065 % zur zentralen Referenz), alle lokalen Solves Status 18.
Verlauf des dualen Maximalquotienten: 107 (1000), 33 (4000), 13.4 (10000), 7.7 (20000),
5.1 (28000), 2.3 (44000), ≤ 1 (61187). Der Median liegt ab ~16000 bei etwa 1–1.5; die
Endphase wird von wenigen Rängen bestimmt.

**Reproduzierbarkeit.** Die frische, ununterbrochene Wiederholung (Budget 77000, Checkpoints alle
5000) endet bei derselben Iteration 61187 mit bitgleichen Zuständen, Multiplikatoren und
Konsenswerten in allen gemeinsamen Checkpoints (40000, 50000, 60000, 61187) und identischen
Endprotokollen aller 118 Agenten. Einziger Unterschied ist der gespeicherte Budgetparameter
(70000 gegenüber 77000), der die Iterate nicht beeinflusst. Die Kernaussage beruht damit nicht
mehr auf einem Lauf mit Fortsetzung. Schleifenzeit lokal 3891 s. Nachweis:
`validation/edge_rho_20260915/strict_repeat/`.

Qualitätsmeilensteine (`tools/iteration_budget.py`): zulässig und ±1 % ab ≤ 9000,
±0.1 % ab ≤ 11000; Toleranzstufe 1e-4 (alle Ränge) ab ≤ 16000 bei zulässigem Zustand.

## Einordnung und offene Punkte

- Die Variante wurde am selben Testfall ausgewählt (ρ₀, p). Das ist im Abstimmungsprotokoll
  offenzulegen; die Kampagne verwendet die hier gewählten Werte eingefroren.
- Erledigt: frische Wiederholung bitgleich (61187); Toleranz 1e-4 an frischem Lauf bestätigt
  (15219, `docs/TOLERANCE_BUDGET.md`); Start aus der zentralen Lösung in `docs/INIT_RHO_STUDY.md`.
- Robustheit bei Toleranz 1e-4 (frische Läufe, Budget 30000, `validation/edge_rho_20260915/robustness_tol1e4/`):

  | Einstellung | Ergebnis | Endquotient primal / dual | Kosten | max. ΔP (p.u.) |
  |---|---|---:|---:|---:|
  | p=1, ρ₀=1e6 (gewählt, Kampagnentest) | konvergiert nach 15219 | ≤ 1 / ≤ 1 | +0.059 % | 4.2e-5 |
  | p=0.5, ρ₀=1e6 | konvergiert nach 17529 | 0.0021 / 1.0 | +0.018 % | 5.0e-5 |
  | p=1, ρ₀=3e6 | nicht konvergiert in 30000, zulässig | 0.00095 / 1.35 | +0.105 % | 7.4e-6 |

  Die Konvergenz hängt nicht an einem Einzelpunkt: auch p=0.5 konvergiert mit ähnlichem Aufwand.
  Zu großes ρ₀ zeigt dasselbe Muster wie bei den Partitionen (Konsens früh, duales Residuum
  fällt langsam). Die gewählte Einstellung p=1, ρ₀=1e6 bleibt unverändert.
- Die langen Zeiten lokal (≈ 100 ms je Iteration) sind durch 118 Ränge auf 16 logischen CPUs
  überbelegt und kein Maß für den Cluster.
