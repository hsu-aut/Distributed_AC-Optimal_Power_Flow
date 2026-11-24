# Konvergenzprüfung und asynchrone Variante

τ = 0 entspricht dem synchronen Verfahren. Läufe mit τ ≥ 1 erreichen das Budget ohne Konvergenz und sind nicht AC-zulässig.

| Lauf | Prüfung alle K Iterationen | Verzögerung τ | Iterationen | Budget | konvergiert | AC-zulässig | Schleifenzeit [s] | ms je Iteration |
|---|---|---|---|---|---|---|---|---|
| k118_K1 | 1 | – | 15219 | 20000 | ja | ja | 137.2 | 9.01 |
| k118_K10 | 10 | – | 15220 | 20000 | ja | ja | 94.3 | 6.19 |
| k118_async_tau0 | 10 | 0 | 15220 | 20000 | ja | ja | 97.6 | 6.41 |
| k118_async_tau1 | 10 | 1 | 20000 | 20000 | nein | nein | 133.6 | 6.68 |
| k118_async_tau2 | 10 | 2 | 20000 | 20000 | nein | nein | 128.4 | 6.42 |
| k118_async_tau5 | 10 | 5 | 20000 | 20000 | nein | nein | 132.8 | 6.64 |
| k16_K1 | 1 | – | 2506 | 3200 | ja | ja | 47.8 | 19.07 |
| k16_K10 | 10 | – | 2510 | 3200 | ja | ja | 44.8 | 17.83 |
| k16_async_tau0 | 10 | 0 | 2510 | 3200 | ja | ja | 45.0 | 17.92 |
| k16_async_tau1 | 10 | 1 | 3200 | 3200 | nein | nein | 57.2 | 17.87 |
| k16_async_tau2 | 10 | 2 | 3200 | 3200 | nein | nein | 57.4 | 17.93 |
| k16_async_tau5 | 10 | 5 | 3200 | 3200 | nein | nein | 57.1 | 17.84 |
| k64_async_tau2 | 10 | 2 | 5700 | 5700 | nein | nein | 48.1 | 8.44 |
| k64_sync_K10 | 10 | – | 4560 | 5700 | ja | ja | 38.2 | 8.38 |
| opt_k118_K1 | 1 | – | 15219 | 20000 | ja | ja | 128.7 | 8.46 |
| opt_k118_K10 | 10 | – | 15220 | 20000 | ja | ja | 89.6 | 5.89 |
| opt_k16_K1 | 1 | – | 2506 | 3200 | ja | ja | 44.5 | 17.75 |
| opt_k16_K10 | 10 | – | 2510 | 3200 | ja | ja | 42.4 | 16.90 |
