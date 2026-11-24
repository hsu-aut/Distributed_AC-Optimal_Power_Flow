# Auswertungspaket Studienarbeit

Erzeugt mit `tools/thesis_package.py` aus den Messreihen vom 15.10. und 19.10.2026.
Alle Abbildungen liegen als PNG (Ansicht) und PDF (Druck, vektoriell) vor; alle Tabellen als
CSV (Semikolon, Excel) und Markdown.

## Abbildungen

| Datei | Aussage | Kapitel |
|---|---|---|
| abb01_zeit_je_iteration | Zeit je Iteration über der Agentenzahl, einheitliches gegenüber kantengewichtetem ρ | Messkampagne |
| abb02_kipppunkt | Rechnen gegenüber Kommunikation über der Teilproblemgröße; Kipppunkt bei ≈ 3.7 Bussen | Auswertung, FF3 |
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
