# Konvergenztoleranz und Iterationsbudget

Stand 15.09.2026. Kampagnen-ρ für Partitionen: 3e5 (T11 entschieden); Bestätigung der
Iterationen bei 1e-4 durch den lokalen Kampagnentest steht aus.
Nachweise: `validation/iteration_budget_20260915/`, Werkzeug `tools/iteration_budget.py`.

## 1. Kriterium

Ein ADMM-Lauf gilt als konvergiert, wenn auf **jedem** Rang primal und dual gilt
(Boyd et al. 2011, je Rang):

    ‖r‖ ≤ min(ε_pri, ε_abs),   ε_pri  = √m·ε_abs + ε_rel·max(‖x‖, ‖z‖)
    ‖s‖ ≤ ε_dual,              ε_dual = √m·ε_abs + ε_rel·‖ρu‖

Zusätzlich ist die unabhängige AC-Prüfung des zusammengesetzten Arbeitspunkts Abnahmebedingung
(Bilanz ≤ 1e-4 p.u., Grenzen ≤ 1e-5). Referenzkriterium: ε_abs = ε_rel = 1e-5.

## 2. Toleranzstufen aus aufgezeichneten Läufen

Werden ε_abs und ε_rel gemeinsam um den Faktor c gelockert, skaliert jeder Quotient
Residuum/Toleranz exakt mit 1/c. Die Iteration, ab der alle Ränge Quotient ≤ c erfüllen, ist
damit die Konvergenziteration für Toleranz c·1e-5 – ohne neue Läufe, mit der Auflösung der
Checkpoints (obere Schranken).

### ρ = 1e6 (Standard-ADMM; 118 Agenten kantengewichtet)

| Konfiguration | 1e-3 | 1e-4 | 3e-5 | strikt 1e-5 |
|---|---|---|---|---:|
| 3 Partitionen | ≤ 1500, zulässig | ≤ 2500, zulässig | ≤ 3000, zulässig | 3124 |
| 4 Partitionen | ≤ 1500, zulässig | ≤ 6000, zulässig | ≤ 8000, zulässig | 10013 |
| 8 Partitionen | ≤ 2000, zulässig | ≤ 6000, zulässig | ≤ 8000, zulässig | 9917 |
| 16 Partitionen | ≤ 2000, **unzulässig** | ≤ 11000, zulässig | ≤ 15500, zulässig | 19374 |
| 118 Busagenten | ≤ 2000, **unzulässig** | ≤ 16000, zulässig | > 30000 | 61187 |

### ρ = 3e5 (Partitionen)

| Konfiguration | 1e-3 | 1e-4 | 3e-5 | strikt 1e-5 |
|---|---|---|---|---:|
| 3 Partitionen | ≤ 936, zulässig | ≤ 936, zulässig | ≤ 936, zulässig | 936 |
| 4 Partitionen | ≤ 1000, **unzulässig** | ≤ 2000, zulässig | ≤ 3000, zulässig | 3025 |
| 8 Partitionen | ≤ 1000, **unzulässig** | ≤ 2000, zulässig | ≤ 2955, zulässig | 2955 |
| 16 Partitionen | ≤ 1000, **unzulässig** | ≤ 4000, zulässig | ≤ 5000, zulässig | 5834 |

### Bestätigung mit frischen Läufen (ρ = 3e5, Toleranz 1e-4)

Lokaler Kampagnentest über `tools/run_campaign.py` mit `cluster/case_studies.yaml`
(`results/local_campaign_tol1e4_20260915/`), exakte Iterationen statt Checkpoint-Schranken:

| Konfiguration | Iterationen | Schranke | Budget | Kosten gegenüber zentral | max. ΔP / ΔQ (p.u.) | ms je Iteration (lokal) |
|---|---:|---:|---:|---:|---:|---:|
| 3 Partitionen | 633 | ≤ 936 | 1200 | −0.00006 % | 2.0e-5 / 2.3e-5 | 42.7 |
| 4 Partitionen | 1723 | ≤ 2000 | 2500 | +0.00090 % | 5.0e-5 / 3.5e-5 | 38.4 |
| 8 Partitionen | 1746 | ≤ 2000 | 2500 | −0.00003 % | 4.2e-5 / 3.4e-5 | 39.9 |
| 16 Partitionen | 3185 | ≤ 4000 | 5000 | +0.00084 % | 4.6e-5 / 2.3e-5 | 43.4 |
| 118 Busagenten, kantengew. ρ₀=1e6 | 15219 | ≤ 16000 | 20000 | +0.05897 % | 4.2e-5 / 4.8e-6 | 70.2 (118 Ränge auf einem Rechner) |

Alle akzeptiert (konvergiert auf jedem Rang, unabhängige AC-Prüfung bestanden). Die
Bilanzfehler liegen um den Faktor 2–5 unter der Abnahmegrenze 1e-4 p.u.; dieser Abstand ist
kleiner als bei strikter Toleranz und wird im Bericht genannt. Die Budgets bleiben wie
eingefroren (sie sind Obergrenzen, keine Zielwerte).

## 3. Folgerungen

- **1e-3 ist nicht vertretbar:** mehrere Konfigurationen erfüllen das Kriterium mit
  unzulässigem Arbeitspunkt.
- **1e-4 liefert in allen untersuchten Konfigurationen zulässige Arbeitspunkte** mit
  Kostenabweichung ≤ 0.06 %.
- Bei ρ = 3e5 liegt die strikte Konvergenz nur etwa 1.5× über 1e-4. Für die Partitionen ist
  damit auch die strikte Toleranz als Kampagnentoleranz tragbar; für 118 Busagenten
  (strikt 61187, 1e-4 ≤ 16000) ist 1e-4 erforderlich, um die Laborzeit einzuhalten.
- ρ = 3e5 ist festgelegt (T11). Budgets: C1 (1e-4) 1200 / 2500 / 2500 / 5000, C3 (1e-5)
  1200 / 3800 / 3700 / 7300, C2 (118, kantengewichtet, 1e-4) 20000.
- T12 (Kampagnentoleranz) wird nach dem lokalen Kampagnentest bestätigt; strikt 1e-5 wird
  in C3 in jedem Fall mitgemessen.

## 4. Budget

    Budget = 1.25 × lokal gemessene Iterationen (gerundet)

Iterationen sind bei gleicher Software deterministisch (Wiederholungen bitgleich,
`validation/benchmark_local_20260914/`); der Zuschlag deckt Gleitkommaunterschiede auf
aarch64. Ein Lauf, der sein Budget ohne Konvergenz erreicht, gilt als nicht konvergiert und
wird so berichtet.
