# Vergleich mit dem DC-OPF (optional, Arbeitsschritt 5)

Stand 15.09.2026. Werkzeug `tools/dc_comparison.py`, Nachweis `validation/dc_comparison_20260915/`.

## Zweck und Abgrenzung

Die Vorarbeit (Iwersen 2025) verteilt den DC-OPF. Um einzuordnen, was der Übergang zum
vollständigen AC-OPF inhaltlich ändert, wird auf **derselben Topologie** ein zentraler DC-OPF
gelöst (verlustlos, |V| = 1, sin Δθ ≈ Δθ, Fluss (θ_i − θ_j − φ)/(x·τ), gleiche quadratische
Kosten, P_g-Grenzen, thermische Grenzen und Winkelgrenzen, sofern vorhanden). Das konvexe QP
wird global gelöst. Das DC-Ergebnis dient **nur dem Vergleich**. Es ist kein AC-zulässiger
Arbeitspunkt und wird weder als Start noch zur Korrektur von AC-Ergebnissen verwendet.

Referenz ist jeweils die akzeptierte zentrale AC-Lösung (Original:
`benchmark_local_20260914/central_t1_r1_1`, PGLib: `pglib_limits_20260915/central_t1`).

## Ergebnisse

| Größe | Original case118 | PGLib case118 |
|---|---:|---:|
| Kosten DC | 125947.88 | 93132.68 |
| Kosten AC (zentral) | 129660.69 | 97213.61 |
| Abweichung DC gegenüber AC | −2.86 % | −4.20 % |
| Verluste AC (p.u.) | 0.774 | 1.387 |
| max. \|ΔP_g\| (p.u.) | 0.864 | 1.893 |
| Σ \|ΔP_g\| (p.u.) | 7.28 | 4.70 |
| max. \|Δθ\| gegen Slack (rad) | 0.128 | 0.267 |
| P_g an einer Grenze: DC / AC | 35 / 17 | 51 / 50 |
| aktive thermische Zweige DC | – (keine Grenzen im Datensatz) | 105, 162 (wie AC) |

## Einordnung

- Der DC-OPF **unterschätzt die Kosten** um 2.9 % bzw. 4.2 %. Das liegt vor allem an den
  vernachlässigten Verlusten (0.77 bzw. 1.39 p.u. bei 42.42 p.u. Last) und an den fehlenden
  Spannungs- und Blindleistungsgrenzen.
- Die **Einsatzentscheidung unterscheidet sich deutlich**: Im Originalfall liegen im DC-OPF
  18 zusätzliche Generatoren an ihrer unteren Grenze. Spannungsband und Q-Grenzen, die im AC-Fall
  aktiv sind (9 Busse an V_max, 9 an Q_max, 4 an Q_min), haben im DC-Modell keine Entsprechung.
- In der PGLib-Variante sind die **thermisch aktiven Zweige in beiden Modellen dieselben**
  (105, 162). Die P_g-Grenzen unterscheiden sich an drei Generatoren.
- Damit ist die Abweichung zwischen DC- und AC-Lösung (Größenordnung Prozent) deutlich größer
  als die Abweichung der verteilten AC-Lösungen von der zentralen AC-Lösung (≤ 0.06 % bei
  Toleranz 1e-4) und größer als die numerisch bestimmte SOCP-Relaxationslücke (0.25 % bzw.
  0.90 %, `docs/FORMULATION.md` §9). Der Mehraufwand
  des AC-Modells ist also inhaltlich begründet.

Nicht untersucht: verteilter DC-OPF auf dem Cluster (Code der Vorarbeit in `legacy/dc/`,
nicht gepflegt). Ein Laufzeitvergleich DC/AC verteilt ist deshalb nicht Teil der Arbeit.
