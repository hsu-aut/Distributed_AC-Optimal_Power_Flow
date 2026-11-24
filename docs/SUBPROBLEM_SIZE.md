# Teilproblemgröße: lokaler Rechenaufwand gegen Kommunikation

Stand 15.09.2026. Beitrag zur Frage der Aufgabenstellung, ab welcher Teilproblemgröße der
lokale Rechenaufwand den Kommunikationsaufwand überwiegt. Lokale Messung (Laptop, WSL);
die Messung auf einem Raspberry Pi folgt im Labor mit demselben Werkzeug.
Nachweise: `validation/subproblem_benchmark_20260915_local/`, `validation/benchmark_local_20260914/`.

## 1. Solverzeit eines lokalen AC-Teilproblems

`tools/subproblem_benchmark.py`: Partitionen von `connected_impedance` für k ∈ {1, 2, 3, 4, 8,
16, 30, 59, 118} (höchstens 12 je k), jedes Teilproblem mit denselben Einstellungen wie im
Agenten (volles AC-Modell, lokales NLP, zentrierte Strafe, ρ=1e6, 1 Thread). Die Konsensziele z
stammen aus der PYPOWER-Referenz mit 0.1 % Störung je Wiederholung; 5 warme Wiederholungen
nach einem kalten Start. Alle 66 Teilprobleme mit Status 18.

| Busse im Teilproblem | Partitionen | Median warm | Median kalt |
|---:|---:|---:|---:|
| 1 | 16 | 2.7 ms | 4.8 ms |
| 2–4 | 16 | 14 ms | 30 ms |
| 5–9 | 16 | 44 ms | 58 ms |
| 10–19 | 8 | 37 ms | 96 ms |
| 20–39 | 5 | 102 ms | 157 ms |
| 40–79 | 4 | 164 ms | 334 ms |
| 118 | 1 | 339 ms | 673 ms |

Näherung über alle Messpunkte: t_warm ≈ 4.3 ms · n (n Busse), mit großer Streuung bei kleinen
Teilproblemen (Zahl der Schnittkanten, Generatoren). Der Benchmark lief parallel zu anderen
Serien; die Absolutwerte sind daher eher zu hoch.

## 2. Zerlegung der Iteration in den ADMM-Läufen

Aus der lokalen Messreihe (je Rang und Iteration, Median-Lauf):

| Konfiguration | mittlere Teilproblemgröße | lokale Optimierung | Kommunikation/Warten | Kommunikationsanteil |
|---|---:|---:|---:|---:|
| 3 Partitionen | ~39 Busse | 32.5 ms | 6.7 ms | 17 % |
| 4 Partitionen | ~30 Busse | 26.8 ms | 7.8 ms | 22 % |
| 8 Partitionen | ~15 Busse | 19.4 ms | 10.0 ms | 34 % |
| 16 Partitionen | ~7 Busse | 16.2 ms | 19.2 ms | 54 % |

„Kommunikation/Warten“ umfasst Nachbaraustausch, globale Konvergenzreduktion und das Warten
auf den langsamsten Rang. Auf einem einzelnen Rechner ist der eigentliche Datentransport nahezu
kostenlos; der Anteil ist hier überwiegend **Warten auf größere Partitionen**.

## 3. Befund lokal

- Lokal überwiegt die lokale Optimierung bis etwa 8 Partitionen (≥ 15 Busse je Teilproblem);
  bei 16 Partitionen (~7 Busse) überwiegen Kommunikation und Warten.
- Bei 118 Busagenten (2.7 ms je Solve) ist die Iteration klar kommunikations- und
  synchronisationsdominiert.
- Mehr Partitionen erhöhen zugleich die Iterationszahl; die Gesamtzeit ist lokal bei
  3 Partitionen am kleinsten.

## 4. Übertragung auf den Cluster

Auf den Pis sind Solves langsamer (Faktor unbekannt) und der Datentransport kostet echte
Netzwerklatenz. Der Kipppunkt n* ergibt sich aus

    t_solve(n*) ≈ t_comm + t_wait(n*)

mit t_solve aus dem Pi-Benchmark und t_comm aus den Phasenzeiten der Clusterläufe.
Im Labor zuerst `tools/subproblem_benchmark.py` auf einem Pi ausführen, dann diese Tabelle mit
Pi-Werten neu erstellen.
