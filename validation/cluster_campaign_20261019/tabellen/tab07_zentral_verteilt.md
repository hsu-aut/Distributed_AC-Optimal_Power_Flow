# Zentrale Referenz gegenüber dem verteilten Verfahren

Alle Werte auf derselben Hardware (Raspberry Pi 5, 1 Thread je Prozess).

| Verfahren | Agenten | Jobzeit [s] | Schleifen-/Kernzeit [s] | CPU-Summe [s] | Jobzeit gegenüber zentral | CPU gegenüber zentral |
|---|---|---|---|---|---|---|
| zentral (1 Pi, 1 Thread) | – | 1.52 | 0.167 | – | 1.0 | – |
| C2 bus118 edge rho k118 | 118 | 95.6 | 87.5 | 10576 | 63 | 63190 |
| C4 partitions edge rho k3 | 3 | 25.2 | 23.6 | 72 | 17 | 430 |
| C4 partitions edge rho k4 | 4 | 83.1 | 81.1 | 326 | 55 | 1948 |
| C4 partitions edge rho k8 | 8 | 34.8 | 32.8 | 267 | 23 | 1597 |
| C4 partitions edge rho k16 | 16 | 43.5 | 41.1 | 668 | 29 | 3994 |
| C4 partitions edge rho k32 | 32 | 42.1 | 38.5 | 1266 | 28 | 7567 |
| C4 partitions edge rho k64 | 64 | 41.1 | 35.1 | 2391 | 27 | 14284 |
