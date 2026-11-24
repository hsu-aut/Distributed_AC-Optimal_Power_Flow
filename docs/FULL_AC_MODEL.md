# Vollständiges IEEE118-AC-OPF

Stand: 2026-09-11. Neue Versuche verwenden `topology_ieee118_full.yaml`.
Aktuelle Folgeversuche: [Höhere Rho-Werte ohne lokales Solver-Zeitlimit](HIGH_RHO_RESULTS.md).
Die früheren Ergebnisse mit Kosten um 985.61 gehören zum vereinfachten Modell
und dürfen nicht als Referenz für diesen Fall verwendet werden.

## Daten und mathematisches Modell

Unveränderte Quelle: [MATPOWER 8.1 case118.m](https://raw.githubusercontent.com/MATPOWER/matpower/8.1/data/case118.m),
lokal `data/matpower/case118.m`; SHA256:
`bc2e6f22b4b9e776572885ee4b50e4f4ab2ee0c5577e9126e86d906f14c4b5f7`.
Die Herkunftskommentare und die MATPOWER-Lizenzhinweise bleiben erhalten.
Die Lizenzdatei unterscheidet ausdrücklich zwischen Software und Falldaten.

118 Busse, 54 einzelne Generatoren und 186 physische Zweige, einschließlich
Parallelleitungen. Erhalten sind originale P-/Q-Grenzen, Spannungsgrenzen,
MW-basierte quadratische Erzeugungskosten, Busshunts, Leitungsladung und
Transformatorübersetzungen. Phasenschieber werden unterstützt und synthetisch getestet.
Nur der Referenzwinkel wird auf null gesetzt; die Referenzspannung ist frei
innerhalb der originalen Grenzen. Alle originalen RATE_A sind null, also ohne
thermisches Limit. Es werden keine zusätzlichen Leitungsratings erfunden.

Für einen Zweig mit Serienadmittanz y, Ladung b und komplexem Tap t gelten
Yff=(y+j b/2)/|t|², Yft=-y/conj(t), Ytf=-y/t, Ytt=y+j b/2.
Die komplexe Leistung an jedem Ende ist Si=Vi conj(Yii Vi+Yij Vj).
Busshunts gehen als (Gs-j Bs)|Vi|² in die Leistungsbilanz ein.
Zentral und verteilt gelten dieselben vollständigen, nichtkonvexen Gleichungen.
Der vollständige Modus erzwingt native nichtlineare Gleichungen und harte Grenzen.
Es gibt keine anschließende NR- oder Lastflusskorrektur.

Der Importer ist auf unterstützte statische MATPOWER-OPF-Daten beschränkt und
weist unter anderem mehrere Generatoren am selben Bus zurück. Das betrifft
diesen IEEE118-Fall nicht. Vollständig bedeutet hier das statische AC-OPF dieses
Quelldatensatzes, ohne zusätzliches N-1-, Dynamik- oder diskretes Schaltmodell.

## Referenz und Prüfungen

| Verfahren | Kosten | lokale Laufzeit | Ergebnis |
|---|---:|---:|---|
| Gurobi, zentral | 129660.69406511678 | Job 1.23238 s; Optimierung 0.09628 s | AC-zulässig, lokal optimal |
| Unabhängiges PYPOWER/PIPS | 129660.69406242225 | Solver-Aufruf 0.46733 s | AC-zulässig, erfolgreich |
| ADMM, eine Partition | 129660.69406106381 | Job 2.98746 s; 5 Iterationen | konvergiert und AC-zulässig |

Quellen: `results/full118_central_01`, `results/full118_pypower_02`,
`results/full118_partition1_01`. Einzelmessungen mit unterschiedlichen Zeitumfängen,
noch kein wiederholter Laufzeitbenchmark. Gurobi verwendet den lokalen NLP-Modus;
die Übereinstimmung zweier Solver liefert keine globale Optimalitätsgarantie.

Die PYPOWER-Implementierung in pandapower 2.14 hat einen Fehler bei leerer Menge thermisch begrenzter Zweige.
Nur für diese unabhängige Referenz werden mathematisch redundante Schranken
aus den Originalspannungsgrenzen und |Yf| beziehungsweise |Yt| abgeleitet:
Sf_max <= Vf_max (|Yf| Vmax), analog am Gegenende, mit Faktor 1+1e-6.
Sie beschränken keinen ursprünglich zulässigen Zustand. Rohdaten und das
Gurobi-Modell bleiben ohne thermische Ratings. Der erste fehlgeschlagene
Referenzversuch bleibt als Fehlerhistorie erhalten.

19 automatisierte Tests bestehen. Sie prüfen unter anderem die vollständige
Ybus gegen PYPOWER (absolute Toleranz 1e-12), beide Zweigenden bei zufälligen
komplexen Spannungen (1e-11), synthetischen Tap mit 17 Grad Phasenverschiebung,
Originalkosten und Grenzen sowie den euklidischen ADMM-Winkelkonsens.
Die unabhängige AC-Prüfung verwendet komplexe Ströme, nicht den Modellhelper.

## ADMM und bisherige Grenzen

Das Verfahren verwendet lokale Kopien von Grenzspannungen und -winkeln,
quadratische Konsensstrafen, den Mittelwert von x+u als z-Schritt und u+=x-z.
Der vollständige Modus erlaubt derzeit nur Relaxation 1, optimierte
Nachbarkopien und strikte Prüfungen. Winkelkonsens und Residuen werden im
selben reellen Koordinatensystem wie die quadratische Zielfunktion berechnet;
ein früheres modulo-2-pi-Wrapping wurde entfernt.
Strikte lokale Schritte müssen einen optimalen oder lokal optimalen Solverstatus
mit vorhandener Lösung liefern. Ein Zeitlimit mit Zwischenlösung reicht nicht aus.
Diese zusätzliche Statusprüfung wurde nach dem Start der beiden Rho-Piloten ergänzt;
Bei beiden Rho-Piloten wurden jeweils alle 32000 lokalen Schritte mit Status
18 (lokal optimal) beendet. Ihre fehlende Konvergenz ist somit nicht auf
akzeptierte Zeitlimit-Zwischenlösungen zurückzuführen.

Akzeptiert wird ein Lauf erst bei primaler **und** dualer Konvergenz sowie
unabhängiger vollständiger AC-Zulässigkeit. P-/Q-Bilanztoleranz: 1e-4 p.u.
(0.01 MW/Mvar auf 100 MVA); weitere Grenztoleranzen: 1e-5.
Ein niedrigerer Kostenwert eines unzulässigen Zustands ist kein Qualitätsgewinn.
Nichtkonvexes ADMM hat hier keine nachgewiesene globale Konvergenzgarantie.

Mit acht Partitionen und Rho 1e7 ist der Zustand nach 25000 Iterationen
AC-zulässig (maximales P-Residuum 1.20322e-5 p.u.), aber ADMM noch nicht
konvergiert. Die beiden Laufsegmente benötigen zusammen rund 862.31 s.
Das ist kein erfolgreicher verteilter Laufzeitvergleich. Rho 1000 und 10000
wurden separat von derselben Startkonfiguration aus geprüft; unveränderte
Toleranzen und fehlgeschlagene Versuche bleiben dokumentiert.

| Acht Partitionen, Rho | Iterationen | Jobzeit | max. P-Bilanzfehler p.u. | max. Q-Bilanzfehler p.u. | Ergebnis |
|---|---:|---:|---:|---:|---|
| 1000 | 4000 | 139.868 s | 26.7870 | 3.81853 | nicht konvergiert, unzulässig |
| 10000 | 4000 | 142.088 s | 48.9842 | 24.8961 | nicht konvergiert, unzulässig |
| 1e7 | 25000, zwei Segmente | 862.313 s gesamt | 1.20322e-5 | 3.16934e-7 | AC-zulässig, ADMM nicht konvergiert |

Der letzte zulässige Zustand bei Rho 1e7 kostet 129729.50754, etwa 0.05307 %
über der zentralen Referenz. Die vollständige Auswertung einschließlich
Endzuständen, Manifesten und Solverstatus steht in
`validation/full_ac_20260911_01/report.json`.
Die Rho-1000/10000-Läufe wurden nacheinander ausgeführt; während der Piloten
liefen kurze Funktionstests und Dateiarbeiten. Ihre Zeiten sind daher
explorative Messungen, keine kontrollierten Vergleichsbenchmarks.

Auch busweise mit 118 Agenten scheitert Rho 10000 im Pilotbudget:
2000 Iterationen, 151.180 s Jobzeit, maximale P-/Q-Bilanzfehler
23.0622/28.6462 p.u., keine Konvergenz. Alle 236000 lokalen Schritte
melden Status 18. Diese Einstellung ist derzeit nicht als funktionierender
IEEE118-Lauf zu empfehlen.

Abschließende Kontrollläufe mit der zusätzlichen Statusprüfung bestätigen
die zentrale Referenz erneut (identische Kosten, Job 1.98171 s,
Gurobi 0.09725 s) und ADMM mit einer Partition (5 Iterationen,
Job 2.59507 s, Kosten 129660.69406106381, AC-zulässig).
Sie stehen zusammen mit dem 118-Agenten-Piloten in
`validation/full_ac_20260911_02/report.json`.
Der 3-Bus-Regressionsfall konvergiert mit dem endgültigen Code unverändert
nach 293 Iterationen ohne Nachbearbeitung; Jobzeit 1.78226 s.

## Reproduzieren und nächste Schritte

In der eingerichteten WSL-Conda-Umgebung aus dem Repository:

```bash
python -m unittest discover -s tests -v
python tools/run_ac_local.py --central --scratch --out results/full_central_new
python tools/run_ac_local.py --agents 8 --rho 1000 --iterations 4000 --solve-limit 30 --timeout 600 --scratch --out results/full_rho1000_new
python tools/run_ac_local.py --agents 8 --rho 10000 --iterations 4000 --solve-limit 30 --timeout 600 --scratch --out results/full_rho10000_new
```

Zuerst belastbare rohe ADMM-Konvergenz erreichen und danach 118 Agenten sowie
3, 4, 8, 16 und weitere Partitionierungen auf genau demselben Fall prüfen.
Anschließend wiederholte lokale Messungen mit identischen Zulässigkeitskriterien,
Kostenabweichung zur zentralen Referenz, Iterationen, Jobzeit, Solverzeit,
CPU-Summe und MPI-Zeit durchführen. Erst dann denselben eingefrorenen Stand
auf dem Cluster messen. MPI-Wartezeit enthält Synchronisation; gezählte
Pufferbytes sind keine gemessenen Netzwerkbytes. Die derzeitige globale
Allgather-Kommunikation ist bei der Skalierungsinterpretation zu berücksichtigen.
Ein Geschwindigkeitsvorteil gegenüber der sehr schnellen zentralen Lösung
ist bislang nicht nachgewiesen.

Nächster konkreter Algorithmusschritt: Kosten- und Konsensskalierung offen
ausweisen, dann eine kontrollierte Rho-Reihe zwischen 1e5 und 1e8 auf acht
Partitionen vergleichen. Primal- und Dualresiduen sowie Kosten und AC-Fehler
über Iterationen gemeinsam auswerten. Große Rho-Werte können den Konsens
verbessern und gleichzeitig die Optimierung der Kosten verlangsamen.
Eine eventuelle Kosten-Normierung muss explizit angegeben werden; sie darf
nicht stillschweigend als Erfolg desselben numerischen Rho ausgegeben werden.
Toleranzen bleiben unverändert. Erst bei einem tragfähigen Verlauf längere
Läufe und weitere Partitionen starten.
