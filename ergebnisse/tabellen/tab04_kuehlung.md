# Einfluss der Kühlung

Gleicher Programmstand, gleiche Pi-Zuordnung, gleiche Reihenfolge; einziger Unterschied ist die Lüfterkühlung. Temperatur ist die höchste Knotentemperatur während des Laufs (Median über die Wiederholungen).

| Konfiguration | Temperatur mit Lüfter [°C] | Temperatur ohne Lüfter [°C] | Läufe mit Drosselung (mit) | Läufe mit Drosselung (ohne) | Jobzeit mit Lüfter [s] | Jobzeit ohne Lüfter [s] | Mehrzeit ohne Lüfter [%] |
|---|---|---|---|---|---|---|---|
| C1 partitions k3 | 56.0 | 81.2 | 0 | 2 | 44.1 | 44.4 | +0.7 |
| C1 partitions k4 | 61.5 | 85.1 | 0 | 4 | 95.6 | 102.1 | +6.9 |
| C1 partitions k8 | 65.9 | 84.5 | 0 | 3 | 52.2 | 53.6 | +2.6 |
| C1 partitions k16 | 66.4 | 84.5 | 0 | 3 | 54.6 | 54.6 | +0.1 |
| C2 bus118 edge rho k118 | 75.7 | 86.2 | 0 | 4 | 95.6 | 100.0 | +4.6 |
| C3 strict tolerance k3 | 83.4 | 85.1 | 2 | 3 | 67.2 | 70.2 | +4.5 |
| C3 strict tolerance k4 | 68.1 | 85.1 | 0 | 3 | 168.2 | 195.3 | +16.1 |
| C3 strict tolerance k8 | 65.9 | 83.4 | 0 | 3 | 87.7 | 87.8 | +0.1 |
| C3 strict tolerance k16 | 74.1 | 85.1 | 0 | 3 | 99.0 | 111.1 | +12.3 |
| C4 partitions edge rho k3 | 56.0 | 81.8 | 0 | 2 | 25.2 | 32.1 | +27.0 |
| C4 partitions edge rho k4 | 70.3 | 84.5 | 0 | 3 | 83.1 | 91.1 | +9.7 |
| C4 partitions edge rho k8 | 71.9 | 85.1 | 0 | 3 | 34.8 | 35.4 | +1.6 |
| C4 partitions edge rho k16 | 70.3 | 83.4 | 0 | 3 | 43.5 | 44.9 | +3.3 |
| C4 partitions edge rho k32 | 68.1 | 84.5 | 0 | 4 | 42.1 | 45.5 | +8.2 |
| C4 partitions edge rho k64 | 67.5 | 84.5 | 0 | 4 | 41.1 | 43.9 | +6.8 |
