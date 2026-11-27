# Messreihen vom 19.10.2026: Kühlung, Konvergenzprüfung, Zweiggrenzen, asynchrone Variante

Auswertung der zweiten Laborphase. Rohdaten: OneDrive `2026-10-19` und `2026-10-19_ohne_luefter`;
kompakte Nachweise: `validation/cluster_campaign_20261019/`. Abbildungen und Tabellen für die
Arbeit: `auswertung_studienarbeit/` (erzeugt mit `tools/thesis_package.py`).
Programmstand `b3cb9b7` (Kampagnen), `9ad72e2` (PGLib-Fall), Branch `cluster-inbetriebnahme`.

## 1. Was sich gegenüber dem 15.10. geändert hat

| Änderung | Wirkung |
|---|---|
| Konvergenzprüfung nur alle 10 Iterationen (`--conv-check-every 10`) | spart zwei globale Reduktionen je Iteration |
| Ränge über die 12er-Bänke verteilt statt blockweise | gleichmäßigere Wärmeverteilung |
| Lüfterkühlung (Kampagne 1) bzw. keine Kühlung (Kampagne 2) | kontrollierter Vergleich |
| Stapelzugriff auf Solverattribute im lokalen Schritt | weniger Python-Aufwand je Iteration |
| zusätzlich: PGLib-Fall mit aktiven Zweiggrenzen, Tests zur asynchronen Variante | neue Aussagen |

Die eingefrorenen Fälle C0 bis C4 sind unverändert. Alle drei Messreihen umfassen je 77 Läufe
(67 verteilt, 10 zentral) und **alle sind akzeptiert**; die höchste Budgetausnutzung liegt bei 80 %.
Die Iterationszahlen entsprechen denen vom 15.10., aufgerundet auf das nächste Vielfache von 10
(633 → 640, 15219 → 15220), weil die Konvergenz nur alle 10 Iterationen geprüft wird.

## 2. Konvergenzprüfung alle 10 Iterationen

Isoliert gemessen (Testreihe, gleiche Bedingungen, nur K verändert):

| Konfiguration | K = 1 | K = 10 | Ersparnis |
|---|---:|---:|---:|
| 16 Partitionen, Schleifenzeit | 47.8 s | 44.8 s | 6 % |
| 118 Busagenten, Schleifenzeit | 137.2 s | 94.3 s | **31 %** |
| 118 Busagenten, ms je Iteration | 9.01 | 6.19 | 31 % |

Der Gewinn wächst mit der Agentenzahl, weil die beiden Allreduce-Aufrufe je Iteration bei kleinen
Teilproblemen den größten Anteil haben. Zusammen mit dem Stapelzugriff auf die Solverattribute
sinkt die Jobzeit der knotenscharfen Verteilung von 145.4 s (15.10.) auf 95.6 s, also um 34 %.
Der Preis sind höchstens neun zusätzliche Iterationen. Die Kosten der Lösung ändern sich nicht.

## 3. Einfluss der Kühlung (kontrollierter Vergleich)

Gleicher Programmstand, gleiche Rangzuordnung, gleiche Reihenfolge; einziger Unterschied ist die
Lüfterkühlung (`tab04_kuehlung`, `abb09`, `abb10`).

| Größe | mit Lüfter | ohne Lüfter |
|---|---:|---:|
| Temperatur während der Läufe (Median) | 67.0 °C | 84.5 °C |
| höchste Temperatur | 84.0 °C | 87.3 °C |
| Läufe mit Drosselungsmeldung | 4 von 80 (5 %) | 50 von 80 (62 %) |
| Mehrzeit ohne Kühlung | – | 0 bis 27 %, meist 2 bis 10 % |

Zwei Befunde:

1. **Die Kühlung wirkt deutlich.** Mit Lüfter bleiben die Knoten im Mittel 17 K kühler, und
   Drosselungsmeldungen verschwinden fast vollständig. Die offene Frage vom 15.10. („ist die
   Zeitstreuung thermisch?“) ist damit beantwortet: ja, sie ist thermisch.
2. **Die Numerik bleibt unberührt.** Beide Kampagnen liefern je Wiederholung **exakt dieselben
   Kosten** – auch dort, wo Wiederholungen untereinander abweichen (Abschnitt 4). Die Temperatur
   beeinflusst ausschließlich die Laufzeit, nicht das Ergebnis.

Für die Arbeit heißt das: Zeitangaben aus der gekühlten Kampagne verwenden; die ungekühlte Reihe
belegt die Größe des Effekts.

## 4. Reproduzierbarkeit: bitgleich bis 16 Agenten

| Agentenzahl | Wiederholungen untereinander | relative Streuung der Kosten |
|---|---|---:|
| 3, 4, 8, 16 (alle Fälle) | bitgleich | 0 |
| 32 | abweichend | 2·10⁻⁹ |
| 64 | abweichend | 1.8·10⁻⁸ |
| 118 | abweichend | 3.8·10⁻⁹ |

Die Abweichung ist **kein Zufall**: Beide Kampagnen (mit und ohne Kühlung) liefern je
Wiederholungsindex exakt dieselben Werte. Sie hängt an der Rangzuordnung, die der Kampagnenstarter
je Wiederholung über die Bänke rotiert. Die naheliegende Ursache ist die uneinheitliche Software:
19 der 118 Knoten laufen unter Debian 13 statt Debian 12, 18 davon mit Python 3.13.5 und NumPy 2.2.4
statt 3.11.2 mit NumPy 1.26.4 (`preflight_k118.json`, Preflight meldet `ok: false`). Je nach Platzierung rechnen andere Ränge auf
diesen Knoten.

**Saubere Prüfung (offen, Aufwand ca. 10 Minuten):** dieselbe Konfiguration zweimal mit *identischem*
Hostfile starten. Sind die Ergebnisse dann bitgleich, ist die Platzierung als Ursache belegt; danach
Knoten vereinheitlichen und eine Wiederholung zum Nachweis messen.

Bis dahin lautet die belastbare Aussage: *Bei fester Platzierung und festem Programmstand sind die
Läufe reproduzierbar; über verschiedene Platzierungen hinweg stimmen die Kosten ab 32 Agenten nur
bis auf etwa 10⁻⁸ relativ überein.*

## 5. Skalierung, Zeitzerlegung und Kipppunkt

Hauptergebnisse der gekühlten Kampagne (`tab02_skalierung`, `abb01` bis `abb03`):

| Agenten | Busse Ø | Iterationen | Jobzeit | ms/Iteration | Rechnen | MPI |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 39.3 | 360 | 25.2 s | 65.5 | 81 % | 18 % |
| 8 | 14.8 | 1130 | 34.8 s | 29.0 | 69 % | 30 % |
| 16 | 7.4 | 2510 | 43.5 s | 16.4 | 60 % | 38 % |
| 32 | 3.7 | 3670 | 42.1 s | 10.5 | 49 % | 49 % |
| 64 | 1.8 | 4560 | 41.1 s | 7.7 | 36 % | 62 % |
| 118 | 1.0 | 15220 | 95.6 s | 5.75 | 29 % | 67 % |

(Kantengewichtetes ρ; die Reihe mit einheitlichem ρ steht in derselben Tabelle.)

**Kipppunkt: 3.7 Busse je Teilproblem** (`abb02`), gegenüber 4.5 am 15.10. Er verschiebt sich zu
kleineren Teilproblemen, weil die Kommunikation durch die seltenere Konvergenzprüfung billiger
geworden ist – das Verhältnis, nicht die Physik, hat sich geändert.

Die lokale Optimierung kostet weiterhin etwa **1.4 ms je Bus und Iteration**; die MPI-Zeit fällt
nicht unter etwa **3.9 ms je Iteration** (vorher 6.4 ms). Die Lastspreizung zwischen langsamstem
und schnellstem Rang steigt von 1.6 (3 Partitionen) auf 6.4 (118 Agenten) und ist damit weiterhin
der Hauptanteil der Wartezeit.

## 6. Zentral gegen verteilt

| Verfahren | Jobzeit | Kernzeit | CPU-Summe | Faktor Jobzeit |
|---|---:|---:|---:|---:|
| zentral (1 Pi, 1 Thread) | 1.52 s | 0.167 s | – | 1 |
| 3 Partitionen (kantengew.) | 25.2 s | 23.6 s | 72 s | 17 |
| 118 Busagenten | 95.6 s | 87.5 s | 10576 s | 63 |

Der Abstand ist durch die Optimierungen von 19× auf 17× (bester Fall) und von 96× auf 63×
(knotenscharf) gesunken. Die Kernaussage bleibt: **kein Laufzeitvorteil des verteilten Verfahrens**
auf diesem Testsystem.

## 7. Fall mit aktiven Zweiggrenzen (PGLib) auf dem Cluster

19 Läufe, zentrale Referenz 97213.6074 (`tab06_zweiggrenzen`). Alle akzeptiert.

| Toleranz | Konfigurationen | Kostenabweichung | max. thermische Verletzung |
|---|---|---:|---:|
| 1e-4 | 3, 4, 8, 16 Partitionen | −0.0020 % bis +0.0001 % | ≤ 8.7·10⁻⁶ |
| 1e-5 | 3, 4, 8, 16 Partitionen | ≤ 0.0002 % | ≤ 1.5·10⁻⁶ |

Damit ist die Einhaltung von Zweiggrenzen erstmals **auf dem Cluster** belegt und nicht nur lokal.
Die thermischen Grenzen werden eingehalten; die Werte liegen unter der Abnahmegrenze 1e-5.

## 8. Asynchrone Variante (Vorversuch, nicht Teil der Kampagne)

| Lauf | Verzögerung τ | Iterationen | Ergebnis |
|---|---:|---:|---|
| 16 Partitionen | 0 | 2510 | konvergiert, identisch zum synchronen Verfahren |
| 16 Partitionen | 1, 2, 5 | 3200 (Budget) | **nicht konvergiert, nicht AC-zulässig** |
| 64 Partitionen | 2 | 5700 (Budget) | nicht konvergiert |
| 118 Busagenten | 1, 2, 5 | 20000 (Budget) | nicht konvergiert |

Die Zeit je Iteration ändert sich dabei kaum (17.9 statt 17.8 ms bei 16 Partitionen; 6.4 bis 6.7
statt 6.2 ms bei 118 Agenten). Das bestätigt die Analyse aus den Zeitmessungen: Der kritische Pfad
ist die Rechenzeit des langsamsten Rangs, und die wird durch Asynchronität nicht kürzer. Gewonnen
wird nur die Wartezeit der schnellen Ränge, verloren wird Konvergenz. Für die Arbeit ist das ein
sauberes negatives Ergebnis für den Ausblick.

## 9. Abbruchkriterium in den Kampagnenläufen

Beide Formen des Kriteriums werden je Iteration mitgeschrieben. Die duale Größe, die den Lauf
beendet, ist das elementweise Änderungsmaß ‖ρ Δz‖ ohne Aᵀ-Zusammenfassung, nicht das kanonische
duale Residuum (`docs/FORMULATION.md` §4.2). Über den ganzen Lauf bleiben die beiden dualen
Abbruchquotienten innerhalb von ±8 % (16 Partitionen) beziehungsweise ±5 % (118 Busagenten).
Am Abbruchzeitpunkt hätte die Boyd-Form bei 16 Partitionen eine Prüfung früher gestoppt, bei
118 Busagenten eine Prüfung später. Zahlen und Abbildungen: `docs/CRITERION_COMPARISON.md`,
Nachtrag, sowie `abb13`/`abb14` des Auswertungspakets.

## 10. Offene Punkte

1. **Homogene Umgebung herstellen** (19 Knoten angleichen), danach eine Konfiguration wiederholen,
   bis der Preflight `ok: true` meldet. Damit entfällt auch die Unsicherheit aus Abschnitt 4.
2. **Platzierungstest:** zweimal derselbe Hostfile, um die Ursache der Abweichung ab 32 Agenten zu
   belegen.
3. Optional: Takt festsetzen, falls Zeitmessungen weiter stabilisiert werden sollen. Nach den
   Daten der gekühlten Kampagne ist das nicht mehr nötig.
