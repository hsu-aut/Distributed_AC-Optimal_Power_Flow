# Abbruchkriterium: Konvention des Lösers gegen Boyd-Matrixform

Stand 15.09.2026. Herleitung: `docs/FORMULATION.md` §4.1–4.2. Werkzeug `tools/criterion_comparison.py`,
Nachweis `validation/criterion_check_20260915/`.

## Frage

Das Kriterium, das die Läufe beendet, fasst das duale Residuum nicht über Aᵀ zusammen und
verwendet andere Normen in den Toleranzen als die Matrixform nach Boyd et al. (2011). Sind die
berichteten Iterationszahlen deshalb an eine Sonderkonvention gebunden?

## Vorgehen

Die eingefrorenen Kampagnenkonfigurationen (Partitionierer `connected_impedance`, ρ = 3e5,
flacher Start) wurden mit engerer Toleranz 2e-6 gerechnet. Jede Iteration zeichnet die Quotienten
Residuum/Toleranz für drei Kriterien auf: Löser je Rang (Entscheidung des Laufs), Boyd-Matrixform
je Rang und Boyd-Matrixform global. Werden ε_abs und ε_rel gemeinsam um c skaliert, skaliert jeder
Quotient exakt mit 1/c; daraus folgt die erste Iteration jeder Toleranzstufe.

**Konsistenzprüfung:** Das Löserkriterium ergibt aus diesen Läufen genau die zuvor mit Toleranz
1e-4 bzw. 1e-5 gemessenen Iterationszahlen (633 / 1723 / 1746 / 3185 und 936 / 3025 / 2955 / 5834).

## Ergebnisse (Partitionen)

Erste Iteration, ab der das Kriterium auf allen Rängen (bzw. global) erfüllt ist:

| Partitionen | Stufe | Löser je Rang | Boyd je Rang | Boyd global |
|---:|---:|---:|---:|---:|
| 3 | 1e-4 | 633 | 640 | 571 |
| 3 | 3e-5 | 791 | 799 | 730 |
| 3 | 1e-5 | 936 | 943 | 875 |
| 4 | 1e-4 | 1723 | 1717 | 1628 |
| 4 | 3e-5 | 2405 | 2400 | 2312 |
| 4 | 1e-5 | 3025 | 3020 | 2932 |
| 8 | 1e-4 | 1746 | 1763 | 1336 |
| 8 | 3e-5 | 2378 | 2395 | 1979 |
| 8 | 1e-5 | 2955 | 2972 | 2557 |
| 16 | 1e-4 | 3185 | 3198 | 1998 |
| 16 | 3e-5 | 4598 | 4612 | 3408 |
| 16 | 1e-5 | 5834 | 5848 | 4672 |

## Befund

- **Je Rang** lagen die Abbruchzeitpunkte in allen untersuchten Läufen dicht beieinander:
  Abweichung höchstens 17 Iterationen bzw. 1.1 %, in beide Richtungen.
- **Global** wurde die Boyd-Matrixform in diesen Läufen früher erfüllt (3–37 %, am stärksten bei
  16 Partitionen, weil dort einzelne Ränge das Ende bestimmen).
- Das ist ein empirischer Befund für die untersuchten Konfigurationen, **keine allgemeine
  Gleichwertigkeit und keine bewiesene Ordnung** der beiden Kriterien. Das Kriterium des Lösers
  bleibt eine ausdrücklich dokumentierte eigene Konvention je Rang (`docs/FORMULATION.md` §4.2);
  die berichteten Iterationszahlen, Budgets und die Toleranzwahl beziehen sich auf sie und werden
  in der Arbeit so benannt, mit der Boyd-Matrixform als Vergleichsgröße.
- Nicht Teil dieses Vergleichs: die AC-Zulässigkeit des Zustands zu den früheren Iterationen der
  globalen Form. Abnahme verlangt immer die unabhängige AC-Prüfung; die Kampagne verwendet
  unverändert das Kriterium je Rang.

## 118 Busagenten (kantengewichtet)

Lauf mit Toleranz 2e-5 (p = 1, ρ₀ = 1e6), beendet nach 46934 Iterationen, akzeptiert
(Kosten +0.0027 %). Stufen oberhalb 2e-5 folgen aus der Skalierung:

| Stufe | Löser je Rang | Boyd je Rang | Boyd global |
|---:|---:|---:|---:|
| 1e-4 | 15219 | 15281 | 6713 |
| 5e-5 | 28375 | 28447 | 15347 |
| 2e-5 | 46934 | > 46934 (Lauf endet mit dem Löserkriterium) | 33160 |

Konsistenz: 15219 bei 1e-4 entspricht exakt dem Kampagnentest. Auch hier stimmen Löser und
Boyd-Form je Rang überein (+0.4 % bzw. +0.3 % Iterationen für die Boyd-Form); die globale Form wäre
schon nach 44 % (1e-4) bzw. 54 % (5e-5) der Iterationen erfüllt, weil wenige Ränge das Ende bestimmen. Der Befund der Partitionen gilt
damit auch für die knotenscharfe Verteilung.

## Nachtrag: Kampagnenläufe auf dem Cluster (19.10.2026)

Die Läufe vom 19.10. zeichnen beide Kriterien je Iteration mit (Spalten `boyd_a_*` der
Residuenspur). Zwei Läufe der gekühlten Kampagne, Toleranz 1e-4, Prüfung alle 10 Iterationen:

| Lauf | Abbruch nach Löserkriterium | Boyd je Rang erfüllt ab | dualer Quotient am Ende (Löser / Boyd) |
|---|---:|---:|---|
| 16 Partitionen, kantengew. | 2510 | 2500 | 0.996 / 0.990 |
| 118 Busagenten, kantengew. | 15220 | im Lauf nicht erreicht | 1.000 / 1.003 |

Das Verhältnis der beiden dualen Abbruchquotienten bleibt über den ganzen Lauf innerhalb von
±8 % (16 Partitionen) beziehungsweise ±5 % (118 Busagenten); Abbildungen `abb13`/`abb14` des
Auswertungspakets zeigen den Verlauf. Praktisch heißt das: bei 16 Partitionen hätte die
Boyd-Form eine Prüfung früher abgebrochen, bei 118 Busagenten eine Prüfung später — sie ist am
Abbruchzeitpunkt mit 1.003 knapp verfehlt. Die Richtung der Abweichung ist also nicht
einheitlich, und die Aussage bleibt die von oben: dicht beieinander, aber keine Ordnung.

Das primale Kriterium ist in beiden Formen dasselbe Residuum ‖Ax − Ez‖; der Unterschied in den
aufgezeichneten Quotienten (Faktor 4 bis 10) stammt allein aus der Normierung, weil der Löser
zusätzlich das harte absolute Gate ‖r‖ ≤ ε_abs fordert. Am Abbruch ist der primale Teil in beiden
Formen deutlich erfüllt (≤ 0.03 bzw. ≤ 0.005); den Zeitpunkt bestimmt durchweg der duale Teil.
