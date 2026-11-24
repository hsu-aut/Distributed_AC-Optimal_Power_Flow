# Initialisierung, lokale Optima und Parametervariation

Stand 15.09.2026. Teil der Arbeitsschritte 2 (Initialisierung, lokale Optima) und 4
(Variation der Koordinationsparameter). Vollständiger IEEE118-Fall, strikte Toleranz 1e-5,
unabhängige AC-Prüfung, keine Nachbearbeitung.

## 1. Lokale Optima der zentralen Referenz

Nachweis: `validation/central_multistart_20260915/`.

| Startpunkte | Läufe | akzeptiert | Kostenspanne | max. \|ΔV\| | max. \|ΔP_g\| |
|---|---:|---:|---:|---:|---:|
| Standard (lastproportional), flach, PYPOWER-Lösung, PYPOWER ±5 %/±20 % (Seeds 1–5), flach ±20 % (Seeds 1–3) | 16 | 16 | 4.1e-6 | 7.1e-9 p.u. | 2.3e-6 MW |

Die zentrale Referenz ist gegenüber dem Startpunkt robust: alle Starts erreichen dasselbe
lokale Optimum wie Gurobi mit Standardstart und das unabhängige PYPOWER/PIPS
(`validation/pypower_reference_20260915/`). Ein globales Optimalitätszertifikat ist das nicht.

## 2. Initialisierung des verteilten ADMM

Nachweis: `results/init_study_20260915/` (ρ=1e6, `connected_impedance`). Start „zentral“ setzt
V, θ, P_g, Q_g und z aus der zentralen Lösung, u = 0; „gestört“ zusätzlich eine relative
Gaußstörung von 2 % (Seed 1/2), `distributed_opf/initialization.py`.

| Start | 3 Partitionen | 8 Partitionen |
|---|---:|---:|
| flach (Standard) | 3124 | 9917 |
| zentral | 378 | 982 |
| zentral, 2 % gestört, Seed 1 | 1646 | 2886 |
| zentral, 2 % gestört, Seed 2 | 1551 | 1935 |

Alle Läufe strikt konvergiert und AC-zulässig, Kosten innerhalb 1e-5 % der Referenz, max.
Bilanzfehler ≤ 9e-7 p.u., **dieselben aktiven Grenzen** wie zentral (9 Busse an V_max,
17 Generatoren an P_min, 9 an Q_max, 4 an Q_min; `tools/limits_utilization.py`).

Befund: Der Startpunkt verändert die Iterationszahl um bis zu Faktor 10, das erreichte
Optimum in den untersuchten Fällen nicht. Ein zentraler Start ist im verteilten Betrieb nicht
verfügbar; die Kampagne verwendet den flachen Start (Abstimmungsprotokoll T10).

Pilot 118 Busagenten, kantengewichtetes ρ (p=1, ρ₀=1e6), 3000 Iterationen, Start zentral gegen
flach (Checkpoints):

| Iteration | zentral: dual max / Median, zulässig | flach: dual max / Median, zulässig |
|---:|---|---|
| 1000 | 20.6 / 2.3, nein | 107 / 34, nein |
| 2000 | 3.4 / 0.56, ja | 72 / 28, nein |
| 3000 | 2.0 / 0.26, ja (Kosten −0.00001 %) | 48 / 22, nein |

Auch bei 118 Agenten verkürzt der zentrale Start die Konvergenz stark (flacher Start: strikt nach
61187 Iterationen); strikte Konvergenz im Pilotbudget von 3000 noch nicht erreicht.

## 3. Variation von ρ (Partitionen)

Nachweis: `results/rho_sensitivity_20260915/`, ρ=1e6 aus `validation/benchmark_local_20260914/`.
Iterationen bis zur strikten Konvergenz; „n. k.“ = nicht konvergiert im Budget 25000
(Endquotienten max. Rang primal/dual, Zulässigkeit, Kostenabweichung in Klammern):

| ρ | 3 | 4 | 8 | 16 |
|---|---:|---:|---:|---:|
| 1e5 | 465 | n. k. (7.1e3 / 3.4e3, unzulässig, +8.0 %) | n. k. (7.8e3 / 6.2e3, unzulässig, +6.2 %) | n. k. (1.1e4 / 5.7e3, unzulässig, +1.6 %) |
| **3e5** | **936** | **3025** | **2955** | **5834** |
| 1e6 | 3124 | 10013 | 9917 | 19374 |
| 3e6 | 9369 | n. k. (7.6e-4 / 2.4, zulässig, +0.0001 %) | n. k. (1.8e-3 / 2.5, zulässig, +0.0000 %) | n. k. (3.2e-3 / 17.6, zulässig, +0.0036 %) |

Toleranzstufe 1e-4 bei ρ=3e5 (Checkpoint-Schranken): ≤ 936 / ≤ 2000 / ≤ 2000 / ≤ 4000,
jeweils zulässig; 1e-3 bei 4/8/16 unzulässig.

**Befund.** Das Optimum ist beidseitig eingeklammert. Zu kleines ρ (1e5) lässt die primale
Konsensverletzung groß: Die Teilprobleme optimieren nahezu unabhängig, der zusammengesetzte
Zustand ist unzulässig (einzige Ausnahme 3 Partitionen mit wenigen Schnittkanten). Zu großes ρ
(3e6) erzwingt Konsens früh (primal zulässig, Kosten exakt), aber das duale Residuum ρ‖Δz‖
fällt langsam. ρ = 3e5 ist der einzige untersuchte Wert, mit dem alle vier Partitionierungen
strikt konvergieren und akzeptiert werden. Er ist nicht überall am schnellsten: Bei 3 Partitionen
braucht 1e5 nur 465 Iterationen, versagt aber bei 4, 8 und 16. Gewählt wurde 3e5 deshalb als
robuster gemeinsamer Wert für alle untersuchten Partitionierungen (Abstimmungsprotokoll T11). Der Befund gilt für diesen Fall; die Lage des
Optimums hängt von Skalierung und Schnittkanten ab (`docs/LIMITATIONS.md` Nr. 6).

## 4. Variation der Kantenvariante (118 Busagenten)

Siehe `docs/EDGE_RHO_RESULTS.md` (Piloten p ∈ {0.5, 1}, ρ₀ ∈ {3e5, 1e6, 3e6, 1e7}).
Robustheitsläufe p=1/ρ₀=3e6 und p=0.5/ρ₀=1e6 bis Toleranz 1e-4 sind eingereiht.
