# Limitationen und Gültigkeitsbereich

Stand 15.09.2026. Gilt für alle lokalen Ergebnisse und ist für die Clusterergebnisse zu ergänzen.

## Modell und Daten

1. **Ein Testsystem.** Alle Aussagen beziehen sich auf das IEEE 118-Bus-System (Aufgabenstellung).
   Übertragbarkeit auf andere Netze, insbesondere andere Steifigkeitsverteilungen der Zweige,
   ist nicht untersucht.
2. **Keine thermischen Grenzen im Originalfall.** MATPOWER 8.1 `case118` hat RATE_A = 0. Die
   Einhaltung von Zweiggrenzen wird deshalb zusätzlich an der ausgewiesenen Variante
   PGLib-OPF v23.07 `case118` geprüft (Ratings, ±30° Winkelgrenzen); diese Variante hat
   geänderte Kosten, P_max und einzelne Q-Grenzen und ist kein Ersatz des Originalfalls.
3. **Statisches OPF.** Keine N-1-, Zeitreihen-, Dynamik- oder diskreten Schaltmodelle.

## Optimierung

4. **Lokale Optimalität.** Das Problem ist nichtkonvex; Gurobi liefert lokal optimale Lösungen
   (Status 18). 16 zentrale Starts und PYPOWER erreichen dasselbe Optimum, und alle akzeptierten
   verteilten Lösungen treffen dieselben aktiven Grenzen. Ein globales Optimalitätszertifikat
   liegt nicht vor. Die SOCP-Relaxierung nach Jabr liefert numerisch Relaxationswerte von
   129339.54 (Original) und 96334.66 (PGLib), also Relaxationslücken von 0.248 % bzw. 0.904 % zur
   zentralen AC-Lösung. Das ist kein zertifizierter Abstand zum globalen Optimum (primaler
   Zielwert bei Barrier-Abbruch, keine validierte duale Schranke), und eine positive Lücke zeigt
   nicht, dass die Relaxierung inexakt ist, weil die AC-Lösung nur lokal optimal ist.
   `docs/FORMULATION.md` §9, `validation/socp_lower_bound_20260915/`.
5. **Keine Konvergenzgarantie.** Für nichtkonvexes ADMM gibt es ohne weitere Annahmen keine
   Konvergenzgarantie. Konvergenz ist empirisch für die untersuchten Konfigurationen belegt,
   nicht allgemein.
6. **Parameter am Testfall abgestimmt.** Partitionierer, ρ, Kantenvariante (p, ρ₀) und
   Kampagnentoleranz wurden anhand lokaler Vorserien desselben Falls gewählt. Die Auswahl ist im
   Abstimmungsprotokoll offengelegt und für die Kampagne eingefroren. Gegenprobe an der
   PGLib-Variante ohne Anpassung: alle Partitionen bei 1e-4 und 1e-5 akzeptiert
   (`docs/PGLIB_HOLDOUT.md`); ein strukturell anderes Netz wurde nicht untersucht.
7. **Toleranzwahl.** Die Kampagnentoleranz 1e-4 ist die lockerste Stufe, bei der alle
   Konfigurationen der Vorserie zulässige Arbeitspunkte lieferten. Zulässigkeit wird deshalb
   stets unabhängig geprüft und ist Abnahmebedingung; 1e-5 wird zusätzlich berichtet. Die
   Reserve bei 1e-4 ist klein: In der PGLib-Gegenprobe lagen die Bilanzfehler bei 8 und 16
   Partitionen nur 0.3 % bzw. 2 % unter der Grenze 1e-4 p.u. (bei 1e-5 mindestens Faktor 7).
8. **Kantengewichtetes ρ ist eine eigene Verfahrensvariante** (ADMM mit fester diagonaler
   Strafmatrix), nicht das Standardverfahren mit einheitlichem ρ.
9. **Initialisierung.** Standard ist der flache Start. Starts aus der zentralen Lösung
   verkürzen die Konvergenz stark, stehen im echten verteilten Betrieb aber nicht zur Verfügung
   und dienen nur der Einordnung.

Zu 5: Das Abbruchkriterium des Lösers ist eine eigene, dokumentierte Konvention je Rang
(`docs/FORMULATION.md` §4.2). In den untersuchten Läufen lagen ihre Abbruchzeitpunkte höchstens
1.1 % neben der Boyd-Matrixform je Rang und später als bei der globalen Form
(`docs/CRITERION_COMPARISON.md`); eine allgemeine Gleichwertigkeit ist damit nicht gezeigt.

## Messung

10. **Lokale Zeiten sind keine Clusterzeiten.** Laptop mit gemeinsamem Speicher: Datentransport
    nahezu kostenlos; „Kommunikation/Warten“ ist überwiegend Warten auf langsamere Ränge.
    118 Ränge auf 16 logischen CPUs sind überbelegt; 16 Ränge nutzen alle Hyperthreads
    (Streuung bis ~45 %).
11. **MPI-Zeiten enthalten Warten.** Blockierende Aufrufe messen Warten plus Transport;
    Pufferbytes sind Nutzdaten, kein gemessener Netzwerkverkehr.
12. **Jobzeit enthält Prozessstart.** Zentral dominieren Python-Start und Modellaufbau
    (~0.5 s) die Gurobi-Zeit (~0.08 s); deshalb werden Kernzeiten getrennt berichtet.
13. **Checkpoint-Auflösung.** Meilensteine und Toleranzstufen aus Checkpoints sind obere
    Schranken (Abstand 500–2000 Iterationen); neue Läufe schreiben den Residuenverlauf jeder
    Iteration.

14. **Globale Synchronisation.** Der Konsensaustausch ist reine Nachbarkommunikation, die
    Abbruchprüfung aber nutzt je Iteration zwei globale Reduktionen (busweise zusätzlich eine für
    den Solverstatus). Die Zeitmessung weist sie getrennt aus (`convergence_mpi`); ältere Läufe
    (`timing_model = legacy`) zählen Residuenrechnung und Konsolenausgabe mit zur Kommunikation.
15. **C1 ist kein isolierter Skalierungstest.** Mit der Agentenzahl ändert sich die
    Partitionierung; Hardwareeffekt und Gesamtvergleich werden getrennt ausgewertet
    (`docs/MEASUREMENT_PROTOCOL.md` §4).

## Cluster (Kampagne 15.10.2026, `docs/CLUSTER_CAMPAIGN_RESULTS.md`)

16. **Thermisch limitierte Zeitmessung.** Die Pis liefen während der Kampagne im Median bei
    83.4 °C; 60 von 80 Läufen melden Drosselung auf mindestens einem Knoten. Bei identischer
    Arbeit (1723 Iterationen) schwankte die Jobzeit um bis zu 10 % mit dem thermischen Zustand.
    Alle Zeitangaben gelten für einen thermisch limitierten Cluster.
17. **Umgebung nicht homogen.** Der Preflight meldet `ok: false`: 18 von 118 Knoten haben
    Python 3.13.5 mit NumPy 2.2.4 statt 3.11.2 mit NumPy 1.26.4, 13 Knoten ein selbst gebautes
    Open MPI (gleiche Version). Quellcode, Gurobi-Version, Lizenz und Hardware sind identisch.
18. **Bitgleichheit nur innerhalb einer Plattform.** Iterationszahlen sind auf aarch64 und x86-64
    exakt gleich, die Endzustände nicht: Kosten unterscheiden sich relativ um ≤ 2.5e-8.
19. **Aktive Grenzen bei Toleranz 1e-4.** Die verteilten Lösungen treffen bei 1e-4 nicht genau
    dieselben aktiven Grenzen wie zentral (Abweichung des Arbeitspunkts bis 2 MW bei Partitionen,
    bis 16.6 MW bei 118 Agenten, Kosten dennoch ≤ 0.06 %). Bei strikter Toleranz 1e-5 stimmen die
    aktiven Mengen. Derselbe Effekt tritt lokal auf, ist also toleranz- und nicht hardwarebedingt.

## Cluster (vor der Messkampagne offen)

14. Gurobi-13-Unterstützung des nichtlinearen Modus auf aarch64 und die Zahl gleichzeitiger
    Lizenzsitzungen sind vor Ort zu bestätigen.
15. Iterationszahlen sind bei gleicher Software deterministisch; auf ARM können
    Gleitkommaunterschiede sie leicht verändern (25 % Budgetzuschlag).
16. Die Pi-Hardware ist ressourcenbeschränkt; Laufzeitaussagen gelten nur für diesen Cluster.
