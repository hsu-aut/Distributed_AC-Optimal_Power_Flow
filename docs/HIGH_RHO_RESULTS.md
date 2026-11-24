# Höhere Rho-Werte ohne lokales Solver-Zeitlimit

2026-09-11; vollständiges MATPOWER-8.1-IEEE118, harte nichtkonvexe
AC-Gleichungen, rohe ADMM-Iterate ohne NR oder Lastfluss-Nachbearbeitung.
Details zum Modell und der unabhängig bestätigten zentralen Referenz:
[FULL_AC_MODEL.md](FULL_AC_MODEL.md).

## Acht Partitionen

Vier sequentielle Läufe vom selben Startzustand, je 4000 Iterationen.
`--solve-limit 0` lässt das lokale Gurobi-Zeitlimit unbeschränkt.
Ein separater Watchdog von 600 s je Gesamtprozess blieb aktiv, wurde aber
in keinem der vier Läufe erreicht. Abbruch erfolgte jeweils am Iterationsbudget.
Die Solverthreads, Partitionierung, Zielfunktionsskalierung und Toleranzen
blieben unverändert. Alle 128000 lokalen Optimierungen meldeten Status 18
(lokal optimal); kein Zeitlimitstatus und kein akzeptierter Ersatzschritt.

| Rho | Jobzeit s | max. P-Fehler p.u. | max. Q-Fehler p.u. | AC-zulässig | ADMM konvergiert |
|---|---:|---:|---:|---|---|
| 1e5 | 125.719 | 27.2944 | 2.64822 | nein | nein |
| 1e6 | 138.127 | 0.310985 | 0.0115106 | nein | nein |
| 1e7 | 137.032 | 5.33961e-4 | 8.94450e-6 | nein | nein |
| 1e8 | 139.443 | 6.03376e-5 | 3.96566e-7 | ja | nein |

Zulässigkeit umfasst die vollständigen P-/Q-Bilanzen sowie alle originalen
Generator-, Spannungs-, Winkel- und vorhandenen thermischen Grenzen.
Die Bilanzschwelle beträgt weiterhin 1e-4 p.u., weitere Grenzen 1e-5.
Ein fehlgeschlagener ADMM-Lauf wird trotz erfolgreichem MPI-Prozess und
gegebenenfalls zulässigem Zustand weiterhin als `success=false` ausgewiesen.

Der zulässige Zustand bei Rho 1e8 kostet **132628.06142**, also **2.28856 %**
mehr als die zentrale lokale Referenz **129660.694065**. Das ist ein
Kostenvergleich zulässiger Lösungen, keine zertifizierte globale Optimalitätslücke.
Das maximale duale Residuum relativ zur jeweiligen Rang-Toleranz beträgt
noch **1047.77**; für Konvergenz müsste jeder Rang höchstens 1 erreichen.
Bei Rho 1e7 beträgt dieser Quotient 169.24. Größeres Rho verbessert hier
die physikalische Übereinstimmung, beschleunigt aber nicht automatisch die
Annäherung an die stationäre Lösung.

Der Rho-1e7-Endzustand stimmt in sämtlichen V-, Winkel-, P- und Q-Werten
exakt mit dem vorherigen 4000-Iterationen-Lauf mit 30 s lokalem Solverlimit
überein. Für diese Konfiguration war das Solver-Zeitlimit kein Hindernis.

![Normierte Residuen und Kostenverlauf](../validation/highrho_unlimited_20260911_01/rho_diagnostics.png)

Die Grafik zeigt Checkpoints alle 500 Iterationen, keine vollständige
Zeitreihe. Die gestrichelte Linie 1 markiert die jeweilige Prüfschwelle.
Primalquotienten verwenden das zusätzliche strikte absolute Gate;
Dualquotienten enthalten den rangweisen skalierten Multiplikatornormterm.
Kosten unzulässiger Punkte in der letzten Grafik sind keine Lösungsgüte.
Die JSON-Kurven erhalten zusätzlich den vollständigen Zulässigkeitsstatus.

## Reproduzieren

Aus dem Repository in der eingerichteten WSL-Python-Umgebung:

```bash
python tools/run_rho_sweep.py --out results/highrho_new --agents 8 --rhos 1e5 1e6 1e7 1e8 --iterations 4000 --solve-limit 0 --timeout 600
python tools/plot_rho_sweep.py results/highrho_new --central results/full118_central_02 --out results/highrho_new/diagnostics
```

Die Originalversuche liegen unter `results/full118_highrho_unlimited_01`.
Kompakte Evidenz mit Manifesten, Quellcode-Hashes, unabhängig validierten
Endzuständen und Solverstatus liegt unter
`validation/highrho_unlimited_20260911_01`. Vollständige Logs und
Wiederanlauf-Checkpoints bleiben lokal in den Experimentverzeichnissen.
Die Zeitangaben sind einzelne explorative Jobmessungen ohne das anschließende
Kopieren der Artefakte. Während der Serie liefen kurze Auswertungen;
es handelt sich noch nicht um kontrollierte wiederholte Benchmarks.

## Numerische Korrektur für 118 Bus-Agenten

Der unzentrierte 118-Agenten-Versuch mit Rho 1e8 bricht schon beim ersten
x-Schritt nach 12.679 s korrekt ab: mehrere lokale NLPs liefern Status 7
(Iterationslimit), ein Bus Status 13 (suboptimal). Ein neuer Probeversuch
mit 10000 statt 1000 internen NLP-Iterationen scheitert ebenfalls (19.675 s).
Es wurde keine dieser Zwischenlösungen als gültiger ADMM-Schritt übernommen.
Das interne [NLBarIterLimit](https://docs.gurobi.com/projects/optimizer/en/current/reference/parameters.html#parameternlbariterlimit)
ist eine andere Grenze als das ausgeschaltete Solver-Zeitlimit.

Isolierte Reproduktionen der Busse 74 und 62 (nullbasiert) bestätigen
`TimeLimit=inf`. Bei Bus 62 stagniert das lokale duale Residuum bei ungefähr
1.5e-8 über der verlangten Schwelle 1e-9. Bus 74 erreicht zunächst ebenfalls
Residuen um 1e-8, entfernt sich anschließend wieder von diesem Zustand.
`NumericFocus=3` behebt das Problem in diesen Proben nicht.

Die optionale Einstellung `--centered-penalty` schreibt die Strafquadrate
algebraisch gleichwertig um. Für eine lokale Variable x mit aggregiertem
Gewicht w und linearem Konsenskoeffizienten c gilt:

```text
0.5*w*x² + c*x = 0.5*w*(x+c/w)² - 0.5*c²/w
delta = x - target,  target = -c/w
```

Die Optimierung verwendet delta als zusätzliche Variable und die lineare
Gleichung x-delta=target. Nur die für den x-Schritt konstante Verschiebung
entfällt. Wiederholte Kopien derselben Variable werden mit ihrem gesamten
Gewicht berücksichtigt. Bei jedem x-Schritt werden die Zielwerte aktualisiert.
Die ADMM-z-/u-Schritte, Rho, Kostenmaßstab, AC-Gleichungen und Toleranzen
bleiben unverändert. Erzeugungskosten bleiben direkt vergleichbar;
unbereinigte `objective_cost`-Werte der beiden Darstellungen unterscheiden
sich um die genannten Konstanten und dürfen nicht gleichgesetzt werden.

`Aggregate=0` verhindert die Rücksubstitution der Hilfsvariablen; sonstige
Vorverarbeitung bleibt aktiv. Vollständig abgeschaltetes Presolve war in
einer isolierten Vorprobe ungünstig und ist nicht die verwendete Einstellung.
Mit der endgültigen Darstellung werden beide untersuchten Busprobleme bei
unveränderter lokaler Dualtoleranz 1e-9 in jeweils 15 NLP-Iterationen lokal
optimal gelöst. Ein Gurobi-Integrationstest prüft die Gleichwertigkeit bei
Rho 100 und 1e8, drei gemeinsamen Vorkommen und wechselnden Zielwerten.
Die Testsuite besteht mit 20 Tests, einschließlich dieses lizenzpflichtigen
Tests (`RUN_GUROBI_TESTS=1`).

Die Darstellung ist vorerst explizit wählbar und kein stillschweigend
geänderter Standard. Die Fest-Rho-Serie oben stammt vollständig aus der
bisherigen Darstellung. Neue Läufe erhalten eigene Manifeste und Ergebnisse.

### Vollständiger Startlauf und Regression, nachgeprüft am 2026-09-14

Mit 118 Agenten, Rho 1e8 und `--centered-penalty --solve-limit 0` beendet
`full118_buswise_rho1e8_centered_01` alle 4000 Iterationen regulär in
332.010855 s. Sämtliche 472000 lokalen Optimierungen melden Status 18.
Der Endzustand ist noch nicht AC-zulässig: max. P-Fehler 4.280386e-4 p.u.,
max. Q-Fehler 5.286287e-6 p.u. Alle weiteren geprüften Grenzverletzungen
sind null. Erzeugungskosten: 135747.66308, wegen fehlender Zulässigkeit
noch keine akzeptierte Lösungsgüte. ADMM ist nicht konvergiert.

Der 3-Bus-Regressionsfall mit zentrierter Darstellung konvergiert unverändert
nach 293 Iterationen und besteht die vollständige unabhängige AC-Prüfung.
Seine Jobzeit beträgt 3.043391 s; sie ist eine einzelne Funktionsprüfung,
kein belastbarer Geschwindigkeitsvergleich der Darstellungen.

Diese Ergebnisse, die vorausgehenden fehlgeschlagenen Versuche und die
isolierten Solverdiagnosen sind in `validation/centered_118_20260914_initial`
gesichert. Vollständige Original-Logs und Checkpoints liegen weiterhin unter
`results/`. Für eine Fortsetzung mit identischen Parametern:

```bash
python tools/run_ac_local.py --agents 118 --rho 1e8 --batch 118 --iterations 8000 --solve-limit 0 --centered-penalty --timeout 900 --scratch --resume results/full118_buswise_rho1e8_centered_01/logs/checkpoints/iter_00004000 --out results/centered_resume_new
```

`--iterations` ist das gesamte Iterationsbudget einschließlich des bereits
gespeicherten Abschnitts. Jobzeit einer Fortsetzung ist nur deren Segmentzeit;
für Laufzeitvergleiche müssen alle benötigten Abschnitte addiert werden.

### Ergebnis der Fortsetzung bis Iteration 8000

Der Lauf `full118_buswise_rho1e8_centered_resume_20260914` beendet weitere
4000 Iterationen regulär. Alle weiteren 472000 lokalen Optimierungen melden
Status 18; insgesamt sind damit 944000 lokale Schritte ohne abgebrochene
Teiloptimierung nachgewiesen. Rho und sämtliche Toleranzen blieben gleich.

| Kennzahl am Endzustand | Wert |
|---|---:|
| Vollständige unabhängige AC-Prüfung | bestanden |
| Maximaler P-Bilanzfehler | 2.40223e-5 p.u. |
| Maximaler Q-Bilanzfehler | 2.94273e-7 p.u. |
| Übrige geprüfte Grenzverletzungen | 0 |
| Erzeugungskosten | 133226.850998 |
| Kostenabweichung zur zentralen lokalen Referenz | +2.750376 % |
| Größter primaler Quotient zur strikten Toleranz | 0.0065202 |
| Größter dualer Quotient zur Rang-Toleranz | 1367.8642 |
| ADMM konvergiert | nein |
| Jobzeit der Fortsetzung | 477.149986 s |
| Jobzeit beider Abschnitte zusammen | 809.160841 s (13.49 min) |

Die AC-Zulässigkeit allein ist kein Konvergenznachweis: der duale Quotient
muss auf jedem Rang höchstens 1 betragen. Die Zeitangaben stammen aus zwei
an unterschiedlichen Tagen ausgeführten Abschnitten; die Fortsetzung baut
die Modelle neu auf. Sie sind keine kontrollierte Benchmarkserie und liefern
keinen Nachweis eines Geschwindigkeitsvorteils gegenüber der zentralen Lösung.

Die Mehrbus-Regression auf drei Bussen mit zwei Partitionen konvergiert mit
der zentrierten Darstellung unverändert nach 199 Iterationen und besteht
die unabhängige AC-Prüfung. Sie prüft zusätzlich den Pfad mit gemeinsam
genutzten Grenzkopien. Die Endzustände, Manifeste, lokalen Solverstatus,
Residuenquotienten und Regressionsergebnisse liegen unter
`validation/centered_118_20260914_final`.

## Folgerung für weitere Versuche

Rho 1e8 liefert einen zulässigen Zustand, ist bislang aber keine schnelle
konvergierte Lösung. Weitere Erhöhungen sind daher nicht allein durch eine
bessere AC-Bilanz gerechtfertigt. Als nächste Kandidaten kommen Werte
zwischen 1e6 und 1e7 sowie eine explizit dokumentierte Skalierungsanalyse
infrage. Änderungen von Rho während eines Laufes müssen als eigene
ADMM-Variante behandelt werden und die skalierten Dualvariablen korrekt
umrechnen; sie dürfen nicht als unveränderter Fest-Rho-Lauf berichtet werden.
Der nächste konkrete Vergleich für 118 Agenten ist ein frischer Lauf mit
Rho 1e7 und derselben zentrierten Darstellung. Ziel ist schnellere duale
Konvergenz bei unveränderter vollständiger AC-Prüfung; eine weitere Erhöhung
über 1e8 ist durch die bisherigen Ergebnisse nicht begründet.

## Drei Partitionen: Fortsetzung bis 25000 Iterationen (2026-09-14)

Unveränderter vollständiger IEEE118-Fall, festes Rho=1e7, zentrierte
Strafdarstellung, keine Nachbearbeitung und keine Lockerung der Toleranzen.
Fortsetzung aus den Checkpoints bei 2000 und 10000; das Budget zählt kumulativ.

| Iterationen gesamt | Kostenabweichung zur lokalen Referenz | Max. dualer Quotient | AC-zulässig | Jobzeit kumulativ s |
|---|---:|---:|---|---:|
| 2000 | +0.418611 % | 198.4320 | ja | 85.5466 |
| 10000 | +0.070984 % | 51.4577 | ja | 450.7083 |
| 25000 | +0.008756 % | 18.2428 | ja | 1111.5222 |

Endkosten 129672.04667618581 gegenüber 129660.69406511678 zentral.
Maximale P-/Q-Bilanzfehler 5.5976664e-7 / 4.1100015e-7 p.u.; alle übrigen
geprüften Grenzverletzungen null. Maximaler primaler Quotient 0.0012830.
Alle 75000 lokalen Optimierungen über die drei Abschnitte haben Status 18.
Dennoch **nicht ADMM-konvergiert**: der maximale duale Quotient muss <=1 sein.
Die Kurven sinken weiter; Nähe der Kosten ersetzt weder Konvergenz noch ein
globales Optimalitätszertifikat.

Segmentzeiten: 85.546563685 + 365.161728952 + 660.813909308 s.
Jobzeit enthält Prozessstart, Modellaufbau und MPI-Lauf, jedoch keine
Snapshotvorbereitung oder anschließendes Kopieren der großen Logs. Es sind
explorative lokale Messungen mit Neustarts und gelegentlichen lesenden
Zwischenprüfungen, kein kontrollierter wiederholter Benchmark.

Nachweise mit kompakten Endzuständen und Manifesten:
`validation/full_p3_25000_20260914/`. Das Diagramm
`continuation_diagnostics.png` kennzeichnet die drei Abschnitte explizit.
Vollständige Wiederanlauf-Checkpoints bleiben lokal unter
`results/full_p3_rho1e7_resume25000_20260914/logs/checkpoints/iter_00025000/`.

## Rho-Vergleich und erste strikte Drei-Partitionen-Konvergenz

Drei frische Läufe mit derselben graph_aware-Aufteilung, Initialisierung,
zentrierten Darstellung und 4000 Iterationen (alle lokalen Solves Status 18):

| Rho | Jobzeit s | Kostenabweichung % | Max. dualer Quotient | AC-zulässig |
|---|---:|---:|---:|---|
| 1e6 | 180.6634 | +0.000857 | 6.6249 | ja |
| 3e6 | 175.2906 | +0.041649 | 40.1124 | ja |
| 1e7 | 171.0824 | +0.225046 | 104.7343 | ja |

Keiner dieser 4000er-Piloten ist konvergiert. Rho=1e6 ist der beste Kandidat
dieses Vergleichs; die Ergebnisse sind kein universeller optimaler Rho-Wert.
Nachweise: `validation/full_p3_rho_comparison_20260914/`.

Die unveränderte Fortsetzung von Rho=1e6 aus Iteration 4000 stoppt automatisch
bei **Iteration 6848: konvergiert und unabhängig AC-zulässig**.

| Kennzahl | Wert |
|---|---:|
| Erzeugungskosten | 129660.67156390342 |
| Zentrale lokale Referenz | 129660.69406511678 |
| Relative Kostenabweichung | -0.0000173539 % |
| Max. primaler Quotient | 0.00706962 |
| Max. dualer Quotient | 0.99955331 |
| Max. P-Bilanzfehler p.u. | 3.4606912e-6 |
| Max. Q-Bilanzfehler p.u. | 1.8137799e-6 |
| Übrige geprüfte Grenzverletzungen | 0 |
| Lokale Solves insgesamt, alle Status 18 | 20544 |
| Jobzeit Fortsetzung s | 128.207095423 |
| Jobzeit beide Abschnitte s | 308.870478075 |

Die geringe negative Kostenabweichung ist bei den verbleibenden
Bilanzresiduen und numerischen Toleranzen kein Beleg für eine bessere
Lösung oder globale Optimalität. Es handelt sich um den ersten akzeptierten
vollständigen Mehr-Partitions-Lauf dieser Versuchsserie, ohne NR und ohne
Toleranzlockerung. Die zentrale Lösung bleibt lokal deutlich schneller;
308.87 s sind kein Speedup gegenüber den zentralen Jobzeiten von 0.52–0.88 s.

Nachweise: `validation/full_p3_converged_20260914/`.
Lokaler finaler Checkpoint:
`results/full_p3_rho1e6_resume10000_20260914/logs/checkpoints/iter_00006848/`.
Der Ordnername enthält das maximale Budget 10000, nicht die tatsächlich
benötigten 6848 Iterationen. Gesamtlaufzeit und Solverzählungen müssen die
4000er-Vorlaufdaten einschließen. Eine frische Wiederholung ohne Neustart
steht noch aus; Laufzeiten sind weiterhin explorativ.

## Übertragung von Rho=1e6 auf weitere Aufteilungen

Frische Piloten mit 4000 Iterationen, centered penalty, unveränderten
Toleranzen und vollständiger Physik. Alle lokalen Optimierungen haben
Status 18, jedoch ist **keiner dieser vier Endzustände AC-zulässig oder
ADMM-konvergiert**.

| Agenten / Partitionen | Jobzeit s | Max. P-Fehler p.u. | Max. Q-Fehler p.u. | Max. dualer Quotient |
|---|---:|---:|---:|---:|
| 4 | 161.6439 | 2.283808 | 0.166334 | 317.07 |
| 8 | 147.7827 | 0.310728 | 0.011524 | 388.28 |
| 16 | 188.1425 | 0.387260 | 0.033376 | 352.12 |
| 118 | 334.1650 | 2.231672 | 1.148199 | 15795.90 |

Bilanzgrenze: 1e-4 p.u. Die Rohkosten dieser unzulässigen Zustände sind
keine Lösungsgüte; besonders die niedrigeren Werte bei 4 und 16 Partitionen
dürfen nicht als Verbesserung der zentralen Lösung interpretiert werden.
Lokale Solverstatuszählungen: 16000 / 32000 / 64000 / 472000, jeweils Status 18.
Nachweise: `validation/full_rho1e6_transfer_20260914/`.

Folgerung: Der Drei-Partitionen-Erfolg ist kein universelles Rho-Rezept.
Bei den anderen Aufteilungen ist Rho=1e6 im getesteten Budget ungeeignet.
Nächster sinnvoller Schritt ist die Analyse von Aufteilung, Grenzkopien,
Skalierung und Residuen, gefolgt von getrennten Kandidaten 3e6 und 1e7.
Für 118 Agenten bleibt ein centered-Pilot bei Rho=1e7 offen. Adaptives Rho
ist weiterhin nur eine mögliche spätere, separat zu dokumentierende Variante.

Die 20 vorhandenen AC-/ADMM-Regressionstests wurden nach dieser Serie
erneut mit aktivierten Gurobi-Integrationstests erfolgreich ausgeführt.
Zum Abschluss sind keine Experimente mehr aktiv.
