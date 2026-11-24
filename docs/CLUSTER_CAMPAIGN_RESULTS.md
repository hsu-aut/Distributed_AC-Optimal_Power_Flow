# Messkampagne auf dem Rechencluster (15.10.2026)

> Fortsetzung: Die Messreihen vom 19.10.2026 (Kühlung, Konvergenzprüfung alle 10 Iterationen,
> Zweiggrenzen, asynchroner Vorversuch) sind in `docs/CLUSTER_CAMPAIGN_20261019.md` ausgewertet.
> Die dort erzeugten Abbildungen und Tabellen sind die für die Arbeit maßgeblichen.

Auswertung der ersten vollständigen Kampagne auf dem Raspberry-Pi-Cluster.
Rohdaten: `campaign_20261015/rohdaten` (OneDrive), kompakte Nachweise:
`validation/cluster_campaign_20261015/`. Kampagnenmanifest `campaign_manifest_20261015T110637393656Z.json`,
Orchestrator pi119, Commit `4aea92f5`, Kampagnendatei-SHA256 `02b8d628…`, 80 geplante Läufe,
Laufzeit 11:06–15:47 UTC.

## 1. Umfang und Abnahme

| Fall | Inhalt | Wdh. | Toleranz | Ergebnis |
|---|---|---:|---|---|
| C0 | zentrale Referenz auf pi1, 1 Thread und alle Threads | 5 + 5 | – | alle akzeptiert |
| C1 | 3 / 4 / 8 / 16 Partitionen, einheitliches ρ = 3e5 | 5 je k | 1e-4 | alle akzeptiert |
| C2 | 118 Busagenten, kantengewichtet (p = 1, ρ₀ = 1e6) | 5 | 1e-4 | alle akzeptiert |
| C3 | 3 / 4 / 8 / 16 Partitionen, strikt | 3 je k | 1e-5 | alle akzeptiert |
| C4 | 3 / 4 / 8 / 16 / 32 / 64 Partitionen, kantengewichtet | 5 je k | 1e-4 | alle akzeptiert |
| N0 | Netzwerk-Benchmark über 118 Ränge | 3 | – | durchgelaufen |

**Abweichung von der eingefrorenen Kampagne.** `cluster/case_studies.yaml` im Repository enthält
nur C0 bis C3 (SHA256 `92966287…`); im Labor lief eine erweiterte Datei (`02b8d628…`) mit den
zusätzlichen Fällen **C4** (kantengewichtetes ρ auf Partitionen, inklusive 32 und 64) und **N0**
(Netzwerk-Benchmark). C0 bis C3 sind unverändert übernommen, die Erweiterung ist ein Zugewinn und
kein Eingriff in die eingefrorenen Fälle. Für die Arbeit muss das als Protokolleintrag
nachgetragen werden (`docs/TUNING_PROTOCOL.md`). Die Herkunft der C4-Budgets ist belegt: Die
Vorstudie T13 (`validation/cluster_campaign_20261015/vorstudie_t13/`) hat am selben Tag auf dem
Cluster ρ für die Kantenvariante auf Partitionen bestimmt (Abschnitt 7a); die Budgets sind
1.25 × die dort gemessenen Iterationen.

**Alle 80 Läufe beendeten mit exit 0 und `success = true`.** Abnahme heißt: alle lokalen Lösungen
lokal optimal, Abbruchkriterium auf jedem Rang erfüllt, unabhängige AC-Prüfung bestanden
(Bilanz ≤ 1e-4 p.u., Grenzen ≤ 1e-5). Kein Lauf erreichte sein Iterationsbudget; die Ausnutzung
lag bei 53–80 %. Werkzeug `tools/aggregate_benchmarks.py` bestätigt für **jede** Konfiguration
bitgleiche Endzustände über alle Wiederholungen.

## 2. Reproduzierbarkeit über Plattformen hinweg

| Konfiguration | Iterationen Cluster | Iterationen lokal | Kosten Cluster | Kosten lokal |
|---|---:|---:|---:|---:|
| 3 Partitionen | 633 | 633 | 129660.619671 | 129660.619671 |
| 4 Partitionen | 1723 | 1723 | 129661.863572 | 129661.860354 |
| 8 Partitionen | 1746 | 1746 | 129660.649014 | 129660.649021 |
| 16 Partitionen | 3185 | 3185 | 129661.783021 | 129661.786175 |
| 118 Busagenten | 15219 | 15219 | 129737.149384 | 129737.149646 |

- **Die Iterationszahlen sind auf aarch64 exakt dieselben wie auf x86-64.** Der Zuschlag von 25 %
  im Iterationsbudget war als Absicherung gegen Gleitkommaunterschiede gedacht und wurde nicht
  benötigt.
- **Die Endzustände sind plattformübergreifend nicht bitgleich:** die Kosten unterscheiden sich in
  der 8. bis 11. signifikanten Stelle (relativ ≤ 2.5e-8). Bitgleichheit gilt innerhalb einer
  Plattform (alle Wiederholungen identisch), nicht zwischen x86-64 und aarch64.
- Code-Identität: Alle 20 Module unter `distributed_opf/` haben auf rank 0 dieselben SHA256 wie der
  lokal geprüfte Stand; abweichend ist nur `tools/run_ac_local.py` (Laborerweiterungen: Thermik,
  Netzwerk-Benchmark). Das Verfahren selbst ist also unverändert.

## 3. Zeitzerlegung und Skalierung (FF3)

Median über Wiederholungen; „x“ = lokale Optimierung, „MPI“ = blockierende MPI-Aufrufe
inklusive Warten, jeweils je Iteration und Rang.

| Fall | k | Busse Ø | Iter | Jobzeit s | ms/Iter | x ms | MPI ms | x/MPI | Rechnen | MPI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | 3 | 39.3 | 633 | 49.6 | 75.6 | 58.4 | 16.5 | 3.5 | 77 % | 22 % |
| C1 | 4 | 29.5 | 1723 | 107.1 | 61.2 | 46.5 | 14.0 | 3.3 | 76 % | 23 % |
| C1 | 8 | 14.8 | 1746 | 58.9 | 32.7 | 22.3 | 9.9 | 2.3 | 68 % | 30 % |
| C1 | 16 | 7.4 | 3185 | 62.5 | 18.8 | 11.1 | 7.4 | 1.5 | 59 % | 39 % |
| C4 | 32 | 3.7 | 3666 | 50.5 | 12.7 | 5.8 | 6.6 | 0.87 | 45 % | 52 % |
| C4 | 64 | 1.8 | 4557 | 50.0 | 9.8 | 3.1 | 6.4 | 0.49 | 32 % | 66 % |
| C2 | 118 | 1.0 | 15219 | 145.4 | 8.9 | 1.9 | 6.8 | 0.28 | 21 % | 76 % |

**Kipppunkt.** Lokale Optimierung und Kommunikation sind gleich groß zwischen 16 und 32
Partitionen; log-lineare Interpolation des Verhältnisses ergibt **etwa 4 bis 5 Busse je
Teilproblem** (16 Partitionen: 7.4 Busse, Verhältnis 1.45; 32 Partitionen: 3.7 Busse, 0.87).
Darüber dominiert das Rechnen, darunter die Kommunikation.

**Kostenmodell.** Die lokale Optimierung kostet etwa **1.5 ms je Bus und Iteration** und bleibt
über alle Partitionierungen konstant (k3 1.48, k16 1.50, k32 1.56, k64 1.70, k118 1.88 ms je Bus);
der Anstieg bei vielen Agenten ist Fixaufwand je Rang. Die MPI-Zeit fällt nicht unter etwa
**6.4 ms je Iteration** – das ist die Synchronisationsgrenze des Clusters.

**Woraus die MPI-Zeit besteht.** Der Netzwerk-Benchmark N0 (118 Ränge, 50 Wiederholungen) misst:

| Größe | einseitig Median | Bandbreite |
|---|---:|---:|
| 8 B | 159 µs (min 116, max 168) | – |
| 1 KiB | 208 µs | 4.9 MB/s |
| 1 MiB | – | 111.6 MB/s |
| Allreduce (1 double, 118 Ränge) | Median 793 µs, max 994 µs | – |

Je Iteration fallen 3 MPI-Aufrufe an (Partitionen: Nachbaraustausch + 2 Reduktionen) bzw. 4 bei
118 Busagenten, mit nur 194–608 Byte Nutzdaten je Rang. Aus Latenz und Allreduce folgen etwa
2 bis 3 ms; gemessen werden 6.4 ms und mehr. **Die Differenz ist Warten auf den langsamsten
Rang, nicht Datentransport.** Die Lastspreizung der x-Schritte beträgt 1.7 (k3) bis 6.2 (k118)
zwischen schnellstem und langsamstem Rang; die Partitionsgrößen schwanken (k16: 5 bis 9 Busse).
Bandbreite ist in keiner Konfiguration ein Engpass.

## 4. Hardwareeffekt (gleiche Konfiguration lokal gegen Cluster)

| Konfiguration | ms/Iter Cluster | ms/Iter lokal (WSL, 16 log. CPUs) | Faktor |
|---|---:|---:|---:|
| 3 Partitionen | 75.6 | 42.7 | 1.77 langsamer |
| 4 Partitionen | 61.2 | 38.4 | 1.59 langsamer |
| 8 Partitionen | 32.7 | 39.9 | 0.82 schneller |
| 16 Partitionen | 18.8 | 43.4 | 0.43 schneller |
| 118 Busagenten | 8.9 | 70.2 | 0.13 schneller |

Bei wenigen, großen Teilproblemen ist ein Pi-Kern deutlich langsamer als ein Laptopkern
(Faktor ≈ 1.6–1.8). Ab 8 Partitionen überwiegt der Vorteil echter Parallelität: Der Laptop muss
die Ränge auf 16 logische CPUs verteilen, bei 118 Rängen ist er massiv überbelegt. Die lokalen
Zeiten taugen deshalb nur als Vorserie, nicht als Referenz.

Zentral: Gurobi-Kernzeit **0.168 s** auf einem Pi gegenüber 0.077 s lokal (Faktor 2.2), Jobzeit
1.52 s gegenüber 0.57 s (Faktor 2.7; enthält `mpirun`-Start, Python-Start 0.30 s, Modellaufbau
0.11 s). Das Modell ist auch auf dem Pi das vollständige: 744 nichtlineare Nebenbedingungen,
0 PWL, 0 SOS, 0 Ganzzahlvariablen, Status 18, Kosten 129660.694065.

## 5. Zentral gegen verteilt (FF2, FF3)

| Größe | Wert |
|---|---|
| zentral, Jobzeit | 1.52 s (Median, 5 Wiederholungen), Kernzeit 0.168 s |
| zentral, alle Threads | praktisch gleich (1.52 s Jobzeit, 0.181 s Kernzeit, also minimal langsamer als mit einem Thread) |
| schnellster verteilter Lauf | C4, 3 Partitionen: 28.1 s Jobzeit, 26.4 s Schleife |
| 118 Busagenten | 145.4 s Jobzeit |
| Faktor Jobzeit | **19× langsamer** (bester Fall) bis 96× (118 Agenten) |
| Faktor Kernzeit | 157× (Schleife gegen Gurobi-Kernzeit) |
| CPU-Summe aller Ränge | 79 s (C4 k3) bis 16408 s (118 Agenten) = 472× bis 97781× der zentralen Kernzeit |

**Es gibt keinen Laufzeitvorteil des verteilten Verfahrens.** Das war die Erwartung und ist
sauber quantifiziert: Der verteilte Ansatz kostet beim IEEE118 mindestens eine Größenordnung mehr
Zeit und zwei bis fünf Größenordnungen mehr CPU-Aufwand. Der Nutzen liegt nicht in der
Geschwindigkeit, sondern darin, dass die Lösung ohne zentrale Datenhaltung entsteht.

## 6. Lösungsgüte und aktive Grenzen (FF2)

Kostenabweichung zur zentralen Referenz (Median über Wiederholungen), maximale Bilanzfehler:

| Konfiguration | Kosten | max ΔP | max ΔQ |
|---|---:|---:|---:|
| C1 3 / 4 / 8 / 16 | −0.00006 % / +0.00090 % / −0.00003 % / +0.00084 % | ≤ 4.9e-5 | ≤ 3.5e-5 |
| C3 3 / 4 / 8 / 16 (strikt) | ≤ 0.00004 % | ≤ 5.0e-6 | ≤ 4.3e-6 |
| C4 3 / 4 / 8 / 16 / 32 / 64 | −0.00013 % bis +0.02375 % | ≤ 7.7e-5 | ≤ 4.4e-5 |
| C2 118 Busagenten | +0.05897 % | 4.2e-5 | 4.8e-6 |

Die Kostenabweichung wächst mit der Agentenzahl, bleibt aber überall unter 0.06 %.

**Aktive Grenzen.** Bei der Kampagnentoleranz 1e-4 treffen die verteilten Lösungen **nicht** genau
dieselben aktiven Grenzen wie zentral:

| Konfiguration | Unterschied zur zentralen Lösung | max ΔV | max ΔP_g |
|---|---|---:|---:|
| 3 Partitionen | ein Generator nicht an Q_max | 2.3e-4 | 0.40 MW |
| 4 / 16 Partitionen | ein Bus nicht an V_max bzw. ein Generator nicht an P_min | 4.7e-4 | 2.1 MW |
| 32 / 64 Partitionen | zwei bis drei Grenzen abweichend | 2.4e-3 | 10.5 MW |
| 118 Busagenten | 3 Busse nicht an V_max, 2 Generatoren nicht an P_min | 1.2e-2 | 16.6 MW |
| C3 strikt (3, 4, 16) | **identische aktive Mengen** | ≤ 4.5e-5 | ≤ 0.20 MW |

Wichtig: **Dieselbe Abweichung tritt bei den lokalen Läufen mit Toleranz 1e-4 identisch auf**
(4 Partitionen Δ 2.04 MW, 118 Agenten Δ 16.57 MW, jeweils exakt dieselben Grenzen). Es ist also
ein Toleranzeffekt und kein Clustereffekt. Die frühere Aussage „verteilt trifft dieselben aktiven
Grenzen wie zentral“ gilt für die strikte Toleranz; bei 1e-4 ist sie auf „zulässig, Kosten
≤ 0.06 %, Arbeitspunkt im Rahmen von ΔP_g ≤ 17 MW“ abzuschwächen. Die Kostenfunktion ist am
Optimum flach: 16.6 MW Umverteilung kosten nur 0.059 %.

Spannungsband und Zweiggrenzen werden in allen Läufen eingehalten
(`max_voltage_violation = 0`, `max_thermal_violation = 0`; der Originalfall hat keine
Zweiggrenzen, siehe `docs/PGLIB_LIMITS.md` für die Variante mit Ratings).

## 7. Kantengewichtetes ρ auf Partitionen (C4) – neuer Befund

| k | C1 (einheitlich) | C4 (kantengewichtet) | Iterationen | Jobzeit |
|---:|---:|---:|---|---|
| 3 | 633 | 360 | −43 % | 49.6 s → 28.1 s |
| 4 | 1723 | 1432 | −17 % | 107.1 s → 92.0 s |
| 8 | 1746 | 1127 | −35 % | 58.9 s → 38.9 s |
| 16 | 3185 | 2506 | −21 % | 62.5 s → 50.4 s |
| 32 | – | 3666 | neu | 50.5 s |
| 64 | – | 4557 | neu | 50.0 s |

Die kantengewichtete Strafmatrix, ursprünglich nur für die knotenscharfe Verteilung eingeführt,
**senkt auch bei Partitionen die Iterationszahl um 17 bis 43 %** und macht 32 und 64 Partitionen
erreichbar, die mit einheitlichem ρ nicht untersucht waren. Der Preis ist eine etwas größere
Kostenabweichung (k32 +0.0071 %, k64 +0.0238 %) und ein etwas größerer Abstand des
Arbeitspunkts. Die Jobzeit erreicht bei 32 bis 64 Partitionen ein Plateau von etwa 50 s: weniger
Iterationsdauer, aber mehr Iterationen.

## 7a. Vorstudie T13: ρ für die Kantenvariante auf Partitionen

15 Läufe auf dem Cluster, Budget je 30000 Iterationen, Toleranz 1e-4
(`validation/cluster_campaign_20261015/vorstudie_t13/`):

| k | ρ₀ = 3e5 (kantengew.) | ρ₀ = 3e6 (kantengew.) | einheitliches ρ |
|---:|---:|---:|---|
| 3 | 360 | 3602 | – |
| 4 | 1432 | 14129 | – |
| 8 | 1127 | 11377 | – |
| 16 | 2506 | 24911 | – |
| 32 | 3666 | nicht konvergiert (30000) | 1e6: 13054; 1e5: nicht konvergiert und unzulässig; 3e6: nicht konvergiert |
| 64 | 4557 | nicht konvergiert (30000) | – |

Befund: ρ₀ = 3e5 ist für die Kantenvariante auf Partitionen durchgängig die beste Wahl, 3e6
braucht das Sieben- bis Zehnfache oder scheitert. Der direkte Vergleich bei 32 Partitionen zeigt
den Gewinn der Kantengewichtung am deutlichsten: **3666 gegenüber 13054 Iterationen** mit dem
besten einheitlichen ρ (1e6), also Faktor 3.6. Die Iterationszahlen der Vorstudie stimmen exakt
mit denen der Kampagne überein.

## 8. Thermik und Drosselung – Einschränkung der Zeitmessung

Der Starter erfasst vor, während und nach jedem Lauf Temperatur und Drosselungszustand aller
Knoten (`*.thermal.json`, Zusammenfassung in `campaign.log`).

- Temperatur während der Läufe: Median **83.4 °C**, Maximum 85.1 °C.
- **60 von 80 Läufen melden Drosselung** auf mindestens einem Knoten (erste 20 Läufe: 10, spätere
  60 Läufe: 50). Die Pis laufen also fast durchgehend am thermischen Limit.
- Auswirkung auf die Zeitmessung bei identischer Arbeit: C1 mit 4 Partitionen, immer 1723
  Iterationen, von 103.3 s (77.9 °C) auf 113.7 s (84.5 °C), also **+10 % allein durch den
  thermischen Zustand**. Streuung je Konfiguration: ≤ 10 % (Ausnahme C4 k3 mit 21.6 % bei sehr
  kurzen Läufen).
- Die zentralen Läufe sind davon kaum betroffen (2.0–2.1 s), weil sie kurz sind und nur einen
  Knoten belasten.

**Was die Drosselung tatsächlich bewirkt hat.** Auswertung aller 7131 Thermik-Stichproben
(alle 30 s, alle Knoten) nach Taktfrequenz und Drosselungsbits:

| Beobachtung | Wert |
|---|---|
| Takt 2400 MHz | 83 % der Stichproben, auch bei 82–85 °C (Median und Minimum dort 2400 MHz) |
| Takt 1500–1900 MHz | 17 %, ausschließlich bei niedrigen Temperaturen, also im Leerlauf |
| Bit „Softlimit Temperatur jetzt aktiv“ | 5.0 % der Stichproben |
| Bit „Takt jetzt gekappt“ bzw. „jetzt gedrosselt“ | 3 von 7131 Stichproben (0.04 %) |
| Bits „aufgetreten“ (Historie seit Start) | 13–20 % |

Eine dauerhafte Taktabsenkung unter Last ist in den Daten also **nicht** nachweisbar; der Takt
blieb bei 2400 MHz. Die gemessene Laufzeitdifferenz von bis zu 10 % korreliert mit der
Temperatur, ihr Mechanismus ist mit 30-Sekunden-Stichproben aber nicht aufgeklärt (kurzzeitiges
Kappen zwischen den Stichproben ist die naheliegende Erklärung). Für eine Wiederholung: Takt fest
einstellen (`scaling_min_freq` = `scaling_max_freq`) und die Thermik alle 5 bis 10 s abtasten.

**Folge für die Arbeit:** Alle Zeitangaben sind als Messung an einem thermisch limitierten Cluster
zu berichten. Die Median-Werte sind robust (bitgleiche Arbeit, 3–5 Wiederholungen), die absolute
Höhe ist aber um bis zu 10 % vom Kühlzustand abhängig. Für eine saubere Zeitreihe wäre ein
zweiter Durchgang mit besserer Kühlung oder längeren Pausen zwischen den Läufen sinnvoll.

## 9. Preflight: heterogene Software auf den Knoten

`preflight_k118.json` meldet **`ok: false`**. Alle 118 Knoten haben 4 Kerne, 8 GB RAM, Gurobi
13.0.0 mit gesetzter Lizenz, bestandenen Ring-Test und identische Quellcode-Hashes. Abweichend
sind 19 Knoten:

| Abweichung | Knoten |
|---|---|
| Python 3.13.5 statt 3.11.2, NumPy 2.2.4 statt 1.26.4 | 18 Knoten (pi2, pi3, pi92–pi100, pi102–pi108) |
| selbst gebautes Open MPI statt Debian-Paket (gleiche Version 4.1.4) | 13 Knoten |

Die Läufe sind trotzdem akzeptiert und innerhalb der Plattform bitgleich reproduzierbar. Für die
Arbeit ist das dennoch eine Einschränkung: Die Umgebung war nicht homogen, und NumPy 2 gegenüber
NumPy 1 kann Gleitkommaoperationen anders ausführen. **Empfehlung:** Knoten vereinheitlichen und
mindestens eine Konfiguration (etwa C1 k8 und C2) danach wiederholen, um zu zeigen, dass sich
nichts ändert.

## 10. Antworten auf die Forschungsfragen

- **FF1 (Teilproblementwurf):** Die eingefrorene Konfiguration hält auf realer Hardware. 3 bis 64
  Partitionen und die knotenscharfe Verteilung auf 118 Recheneinheiten konvergieren strikt und
  liefern AC-zulässige Arbeitspunkte ohne Nachbearbeitung; die Iterationszahlen sind exakt
  vorhersagbar. Die kantengewichtete Strafmatrix ist der klar bessere Koordinationsparameter, auch
  für Partitionen.
- **FF2 (Lösungsgüte):** Kostenabweichung ≤ 0.06 %, Spannungsband und Zweiggrenzen eingehalten.
  Bei Toleranz 1e-4 weicht der Arbeitspunkt an einzelnen Generatoren um bis zu 17 MW ab, bei
  strikter Toleranz werden die aktiven Grenzen exakt getroffen.
- **FF3 (Rechenaufwand):** Der Aufwand je Iteration folgt 1.5 ms je Bus, die Kommunikation hat
  einen Boden von 6.4 ms. Der Kipppunkt liegt bei 4 bis 5 Bussen je Teilproblem. Zentral bleibt
  19× bis 96× schneller; der verteilte Ansatz ist auf diesem Testsystem kommunikations- und
  synchronisationsbegrenzt, sobald die Teilprobleme klein werden.

## 11. Offene Punkte

1. **Homogene Umgebung herstellen** und C1 k8 sowie C2 wiederholen (Preflight muss `ok: true` sein).
   *Offen; siehe `docs/CLUSTER_CAMPAIGN_20261019.md` Abschnitt 4.*
2. ~~Thermisch entspannte Wiederholung~~ **erledigt am 19.10.**: Mit Lüfterkühlung sinkt die
   Temperatur im Median von 83.4 °C auf 67.0 °C, Drosselungsmeldungen von 75 % auf 5 % der Läufe;
   die Laufzeit ohne Kühlung liegt 0 bis 27 % höher, die Kosten sind identisch.
3. **C4 belegen:** Für 32 und 64 Partitionen gibt es im Repository keinen lokalen Vorserienlauf
   (geprüft: kein Lauf mit 32 oder 64 Agenten unter `results/`). Entweder den Pilotlauf, aus dem
   die Budgets stammen, als Nachweis nachreichen oder die Serie lokal einmal nachrechnen. Außerdem
   `cluster/case_studies.yaml` im Repository auf den Laborstand bringen (C4, N0).
4. ~~Zweiggrenzen auf dem Cluster~~ **erledigt am 19.10.**: 19 Läufe der PGLib-Variante auf dem
   Cluster akzeptiert, thermische Grenzen eingehalten.
5. Diagramme: Einzelauswertungen je Lauf liegen unter `auswertung/einzellaeufe/`
   (`tools/run_report.py`: Kennzahlentabelle, Rangtabelle, getrennte Diagramme für primales und
   duales Residuum). Die Sammeldiagramme aus `tools/thesis_figures.py` eignen sich für den
   Überblick, für die Arbeit sind die Einzeldiagramme vorzuziehen.
6. Taktfrequenz für die nächste Messreihe festsetzen (Vorschlag: 1600 MHz Haupt-, 1000 MHz
   Zusatzserie) und daraus die Trennung von Rechen- und Kommunikationsanteil über den Takt
   bestimmen.
