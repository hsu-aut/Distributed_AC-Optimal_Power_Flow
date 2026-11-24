# Phase 5: Zusammenhängende, impedanzbewusste Partitionierung (14. September 2026)

Vollständiges IEEE118-AC-OPF, rohes ADMM, festes ρ=1e6 (einheitlich), zentrierte
Strafdarstellung, α=1, keine Nachbearbeitung, unveränderte Toleranzen. Lokal unter
WSL, Benchmark-Modus des Phase-1-Codes. Einzige Änderung gegenüber den früheren
Partitionsläufen ist die **Zuordnung der Busse zu Agenten**. Die Modellgleichungen und
das ADMM-Verfahren bleiben gleich. Der alte Partitionierer `graph_aware` bleibt Standard in
`tools/run_ac_local.py` und reproduziert alle früheren Läufe.

## Partitionierer `connected_impedance`

`distributed_opf/partitioning.py`, Auswahl mit `--partitioner connected_impedance`.

1. Blattbusse werden nie von ihrem einzigen Nachbarn getrennt; die steifsten Zweige
   (oberes Quantil von |y|) werden zu Superknoten zusammengefasst.
2. k zusammenhängende Regionen wachsen von weit auseinanderliegenden Startpunkten,
   jeweils um den am stärksten angebundenen Nachbarknoten.
3. Größenausgleich in ein Band von ±20 %, danach Verschieben von Grenzknoten, solange
   die Summe der geschnittenen |y| sinkt (Zusammenhang und Band bleiben erhalten).
4. Wiederholung über alle Startbusse und drei Schwellen; Auswahl nach kleinster
   Bandverletzung, dann kleinster Schnittadmittanz. Deterministisch.

| k | Partitionierer | Größen | nicht zusammenhängend | Schnittzweige | max. \|y\| geschnitten | Σ\|y\| geschnitten | Blattbus-Schnitte |
|---|---|---|---:|---:|---:|---:|---:|
| 3 | alt | 40/39/39 | 1 | 28 | 62 | 456 | 0 |
| 3 | neu | 43/44/31 | 0 | 11 | 11 | 77 | 0 |
| 4 | alt | 30/30/29/29 | 2 | 40 | 246 | 942 | 1 |
| 4 | neu | 24/23/36/35 | 0 | 20 | 27 | 179 | 0 |
| 8 | alt | 14–15 | 6 | 57 | 246 | 978 | 3 |
| 8 | neu | 11–18 | 0 | 36 | 27 | 290 | 0 |
| 16 | alt | 7–8 | 9 | 82 | 246 | 1632 | 3 |
| 16 | neu | 5–9 | 0 | 61 | 33 | 556 | 0 |

Tests: `tests/test_partitioning.py` (Vollständigkeit, Zusammenhang, Größenband,
Zweig 67–115 intern, Determinismus, kleinere Schnittadmittanz als `graph_aware`).
Insgesamt 33 Tests bestanden.

## Ergebnisse, ρ = 1e6

Zentrale lokale Referenz: 129660.69406511678 (Gurobi etwa 0.1 s, Jobzeit 0.5–0.9 s).
Fortsetzungen sind seit Phase 1 bitgleich zu ununterbrochenen Läufen; Zeiten der
Abschnitte werden addiert.

| k | Iterationen | ADMM konvergiert | AC-zulässig | Kosten Abweichung | max. P-/Q-Fehler p.u. | Schleifenzeit | CPU-Summe | Solves (alle Status 18) |
|---|---:|---|---|---:|---|---:|---:|---:|
| 3 | **3124** | ja | ja | −0.0000002 % | 1.8e-7 / 2.1e-7 | 123.3 s | 371 s | 9372 |
| 4 | **10013** (10000 + 13) | ja | ja | +0.000009 % | 4.5e-7 / 4.3e-7 | 346.6 s | 1390 s | 40052 |
| 8 | **9917** | ja | ja | +0.0000003 % | 3.8e-7 / 5.1e-7 | 295.8 s | 2374 s | 79336 |
| 16 | **19374** (10000 + 9374) | ja | ja | +0.000008 % | 4.2e-7 / 3.6e-7 | 525.5 s | 8420 s | 309984 |

Zum Vergleich mit dem alten Partitionierer bei gleichem ρ: 3 Partitionen
konvergierten erst nach 6848 Iterationen; 4, 8 und 16 Partitionen waren nach 4000
Iterationen unzulässig (max. P-Fehler 2.28 / 0.31 / 0.39 p.u.).

Beobachtungen (explorativ, lokal überbelegt bei 16 Rängen, keine Benchmarks):
- Die x-Schritt-Zeit je Rang schwankt stark mit der Partitionsgröße
  (k=8: 125–258 s, k=16: 66–278 s im ersten Abschnitt). Kleine Partitionen warten im
  Konsensaustausch auf große (bis 133–156 s je Rang).
- Die Iterationszahl wächst mit k, die Zeit je Iteration sinkt nicht proportional.
  Auf diesem Rechner ist kein Lauf schneller als die zentrale Lösung.
- Die Kosten liegen bei allen Aufteilungen innerhalb von 1e-5 % der zentralen
  lokalen Referenz; das ist kein globales Optimalitätszertifikat.

Nachweise: `validation/phase5_partitions_20260914/` (Manifeste mit Partitionsstatistik,
Endzustände, AC-Prüfungen, Checkpoint-Monitorreihen, Phasenzeiten je Rang, Solverstatus).

## Folgerungen für die weiteren Phasen

- Mit diesem Partitionierer ist eine vollständige Skalierungsreihe
  zentral – 3 – 4 – 8 – 16 Agenten mit akzeptierten, strikt konvergierten Endzuständen
  lokal verfügbar. Das ist die Grundlage für den Laufzeitvergleich auf dem Cluster.
- Nächster lokaler Schritt: wiederholte, ununterbrochene Messläufe (mehrere
  Wiederholungen je k, Iterationsbudget über dem Bedarf) und zentrale Referenz mit
  gleicher Zeitzerlegung, damit Streuung und Messprotokoll feststehen, bevor die Pis
  verfügbar sind.
- Die Größenunterschiede der Partitionen bestimmen die Wartezeit; eine nach
  erwarteter Rechenlast ausbalancierte Aufteilung ist eine mögliche spätere Verbesserung.
- 118 busweise Agenten bleiben mit einheitlichem ρ ungelöst (Phase 2);
  kantengewichtetes ρ folgt am Ende als getrenntes Modell.
