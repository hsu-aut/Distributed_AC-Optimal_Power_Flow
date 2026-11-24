# Lokale Messreihe: zentral gegen verteilt (14. September 2026)

Nach `MEASUREMENT_PROTOCOL.md`. Rechner: Intel Core i7-13620H, Ubuntu unter WSL2
(16 logische CPUs), Python 3.11, Gurobi 13.0.2, Open MPI 5.0.10. Vollständiges
IEEE118-AC-OPF, keine Näherung, keine Nachbearbeitung. Verteilt: rohes ADMM, festes
ρ=1e6, zentrierte Strafdarstellung, Partitionierer `connected_impedance`,
1 Gurobi-Thread je Agent, alle Ränge lösen gleichzeitig, Benchmark-Modus.
Nur frische, ununterbrochene Läufe; Reihenfolge je Wiederholung rotiert.

## Qualität

**Alle 72 Läufe akzeptiert** (30 zentral, 12 verteilt je 3 Wiederholungen für k=3/4/8/16).
Zentral: Solverstatus 18 und AC-Prüfung. Verteilt: strikte primale und duale Konvergenz
auf allen Rängen, alle lokalen Solves Status 18, unabhängige AC-Prüfung.
**Endzustände wiederholter Läufe sind bitgleich** (SHA256 über V, θ, Pg, Qg), die
Iterationszahlen identisch. Die Ergebnisse sind damit deterministisch reproduzierbar.

| Konfiguration | Iterationen | Kostenabweichung zur zentralen Referenz |
|---|---:|---:|
| zentral | – | 0 |
| 3 Partitionen | 3124 | −0.0000002 % |
| 4 Partitionen | 10013 | +0.0000093 % |
| 8 Partitionen | 9917 | +0.0000002 % |
| 16 Partitionen | 19374 | +0.0000085 % |

## Zeiten (Median [Minimum–Maximum] über Wiederholungen)

| Konfiguration | Wdh. | Jobzeit s | Kernzeit s¹ | CPU-Summe s | Gurobi-Summe s | ms je Iteration |
|---|---:|---:|---:|---:|---:|---:|
| zentral, 1 Thread | 15 | 0.570 [0.519–0.722] | 0.083 (Gurobi 0.077) | – | 0.077 | – |
| zentral, alle Threads | 15 | 0.521 [0.519–0.619] | 0.080 (Gurobi 0.075) | – | 0.075 | – |
| 3 Partitionen | 3 | 126.8 [124.7–130.4] | 124.9 | 376 | 272 | 40.0 |
| 4 Partitionen | 3 | 346.1 [340.2–350.2] | 344.3 | 1379 | 920 | 34.4 |
| 8 Partitionen | 3 | 295.9 [289.8–297.1] | 293.7 | 2357 | 1242 | 29.6 |
| 16 Partitionen | 3 | 531.0 [476.5–694.7] | 528.2 | 8454 | 3732 | 27.3 |

¹ zentral: Optimierungsaufruf; verteilt: ADMM-Iterationsschleife.
Zentrale Jobzeit zusätzlich zerlegt: Laden ~0.15 s, Modellaufbau ~0.05 s.

Zeitzerlegung verteilt, Summen über Ränge (Median):

| k | x-Schritt größter / kleinster Rang s | Warten + Austausch (Summe) s | Anteil an Rang-Schleifenzeit | Gurobi je Iteration (Summe über Ränge) | MPI-Pufferbytes |
|---|---:|---:|---:|---:|---:|
| 3 | 123 / 80 | 62 | 17 % | 87 ms | 4.4 MB |
| 4 | 337 / 194 | 154 | 11 % | 92 ms | 24.4 MB |
| 8 | 253 / 120 | 532 | 23 % | 125 ms | 44.4 MB |
| 16 | 483 / 136 | 1922 | 23 % | 193 ms | 141.4 MB |

## Befunde

1. **Kein Geschwindigkeitsvorteil auf diesem Rechner.** Die schnellste verteilte
   Konfiguration (3 Partitionen, 127 s Jobzeit) ist etwa 240-mal langsamer als
   zentral (0.52–0.57 s); bezogen auf die Kernzeit etwa 1500-mal.
2. **Die Zeit je Iteration sinkt mit k** (40 → 27 ms), aber deutlich unterproportional,
   während die Iterationszahl steigt (3124 → 19374). Die Gesamtzeit ist bei
   3 Partitionen minimal.
3. **Mehr Partitionen erhöhen den gesamten Rechenaufwand je Iteration**
   (Gurobi-Summe 87 → 193 ms): viele kleine nichtkonvexe Teilprobleme mit
   Randkopien kosten zusammen mehr als wenige große.
4. **Der größte Rang bestimmt die Iteration.** Die x-Schritt-Zeit variiert je Rang
   um den Faktor 1.5–3.5; kleine Ränge warten, der Warteanteil erreicht 23 %.
5. **Streuung:** bei 3/4/8 Partitionen innerhalb von ~5 %, bei 16 Partitionen bis
   ~45 % (476–695 s). 16 Ränge belegen alle logischen CPUs einschließlich
   Hyperthreads; lokale Werte für 16 Ränge sind daher nur eingeschränkt belastbar.
6. Zentral bringen zusätzliche Gurobi-Threads bei diesem Problem nichts Messbares.

## Einordnung

Diese Werte stammen von einem einzelnen Laptop mit gemeinsamem Speicher; die
Kommunikation ist hier fast kostenlos. Auf einem Cluster aus Raspberry Pis sind
sowohl die lokalen Solves langsamer als auch die Kommunikation teurer. Die lokale
Reihe beantwortet die Speedup-Frage daher nicht, sie liefert aber:
- den reproduzierbaren, deterministischen Referenzstand für den Cluster,
- die Zerlegung, an der sich Clusterergebnisse messen lassen
  (Iterationen sind hardwareunabhängig; ms je Iteration, Warteanteil und
  Kommunikation sind es nicht),
- die Erkenntnis, dass bei IEEE118 die Iterationszahl von ADMM den Vergleich
  dominiert und nicht die parallele Rechenleistung.

Nachweise: `validation/benchmark_local_20260914/` (Zusammenfassung, alle Einzelläufe,
Manifeste, Treiberlog, Testlog). Vollständige Laufordner lokal unter
`results/benchmark_local_20260914/`.
