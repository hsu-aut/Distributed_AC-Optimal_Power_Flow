# Phase 2: 118 busweise Agenten – Piloten mit festem Rho (14. September 2026)

Vollständiges IEEE118-AC-OPF, rohes ADMM, zentrierte Strafdarstellung, keine
Nachbearbeitung, unveränderte Toleranzen. Frische, ununterbrochene Läufe mit dem
Phase-1-Code im Benchmark-Modus (`--log-every 0 --monitor-every 0
--checkpoint-every 500 --timing-trace --mpi-yield`). Kein kantengewichtetes Rho.
Lokal WSL, 118 Ränge auf 16 logischen CPUs: Zeiten nur indikativ.

Ein erster Anlauf ging mit dem Ende einer Sitzung verloren (WSL beendet, Arbeitsverzeichnis
in `/tmp`). Er ist als `bus118_rho1e7_aborted_session_end` gekennzeichnet und wird
nicht ausgewertet. Die Wiederholung lief vom Terminal gelöst mit persistentem Arbeitsverzeichnis.

## Ausgangsanalyse vorhandener 118-Checkpoints

| Lauf | Primal V / θ | Dual ρ‖Δz‖ V / θ | Verlauf |
|---|---|---|---|
| ρ=1e6, It. 1000–4000 | ~2e-2 / ~2e-2 | ~3e4 / ~3e4 | flach, pendelt, keine Konvergenz |
| ρ=1e8, It. 1000–8000 | 3e-5 → 3e-8 / 5e-5 → 2e-7 | 8.4e3 → 1.2e3 / 2.2e4 → 5.8e3 | primal weit unter Toleranz, dual ≈ k^-0.7, θ dominiert |

## Piloten, je 3000 Iterationen

Quotienten gegen die strikten Kriterien je Rang (Konvergenz verlangt ≤ 1 auf allen Rängen).

| Einstellung | Primal max | Dual max | Dual Median | Dual V / θ | max. P-Fehler p.u. | AC-zulässig | Schleife |
|---|---:|---:|---:|---|---:|---|---:|
| ρ = 1e7 | 0.88 | **415** | **44** | 343 / 1338 | 5.0e-4 | nein | 124 s |
| ρ = 3e7 | 0.064 | 1172 | 160 | 917 / 4698 | 2.4e-4 | nein | 131 s |
| ρ_v = 1e8, ρ_θ = 1e7 | 1.73 | 516 | 73 | 1441 / 1365 | 8.9e-4 | nein | 129 s |
| ρ_v = 1e7, ρ_θ = 1e8 | 0.17 | 2624 | 420 | 724 / 11670 | 1.9e-4 | nein | 117 s |

Verlauf ρ=1e7, dualer Maximalquotient: 3609 (500), 1151 (1000), 483 (2000), 415 (3000).
Primalquotient schwankt zwischen 0.9 und 4, Bilanzfehler zwischen 5e-4 und 2e-3.

Befunde:
- Kein Pilot ist konvergiert oder AC-zulässig. Alle lokalen Solves Status 18.
- ρ_θ bestimmt den dualen θ-Anteil direkt; großes ρ_θ ist klar ungünstig.
- Das beste feste ρ im Feld liegt bei etwa 1e7. Der duale Quotient fällt dort sublinear
  (zuletzt kaum noch); bei gleicher Rate wären 10^5–10^6 Iterationen nötig.
  **Festes, einheitliches Rho bringt 118 Busagenten in praktikablem Budget nicht zur Konvergenz.**
- Kleinere ρ (1e6) pendeln, größere (≥ 3e7) verlangsamen die duale Konvergenz:
  das bekannte Rho-Dilemma, hier quantitativ für den Vollfall bestätigt.

## Frische Wiederholung: drei Partitionen, ρ=1e6

Ununterbrochen, ein Abschnitt. **Konvergiert bei Iteration 6848**, derselben Iteration
wie der frühere Zwei-Abschnitte-Lauf, unabhängig AC-zulässig.

| Kennzahl | frisch | früher (zwei Abschnitte) |
|---|---:|---:|
| Iterationen | 6848 | 6848 |
| Erzeugungskosten | 129660.67156399 | 129660.67156390 |
| max. P-/Q-Fehler p.u. | 3.4607e-6 / 1.8138e-6 | 3.4607e-6 / 1.8138e-6 |
| max. Zustandsdifferenz V, θ, Pg, Qg | 3.7e-10 | – |
| Schleifenzeit | 254.8 s | 308.9 s Jobzeit gesamt |

Die Differenz von 3.7e-10 entspricht dem in Phase 1 nachgewiesenen Warmstartverlust
des alten Fortsetzungscodes. Der Drei-Partitionen-Erfolg ist damit **reproduziert**.
Phasenzeiten je Rang: x-Schritt 228.9 s, Konsensaustausch (inkl. Warten auf die
langsamste Partition) 23.0 s, übrige Phasen zusammen etwa 2 s. Zentral: Gurobi
etwa 0.1 s, Jobzeit 0.5–0.9 s.

Nachweise: `validation/phase2_pilots_20260914/` (Checkpoint-Kurven, Diagramm,
Monitorreihen mit V/θ-Aufteilung, Manifeste, Phasenzeiten, Solverstatus).

## Phase 2b: zwei ausgewiesene Verfahrensvarianten (118 Busagenten)

Beide Varianten sind eigene, getrennt benannte ADMM-Varianten. Der Standardpfad
(α=1, festes ρ) ist nach dem Umbau in allen fünf Referenzfällen von Phase 1
**bitgleich** geblieben (`validation/phase2b_variants_20260914/equivalence/`).
29 Tests bestehen, davon 4 neue. Kantengewichtetes ρ bleibt bewusst ausgeschlossen
und wird erst am Ende als separates Modell untersucht.

**Korrigierter Fehler (Über-Relaxation):** Der z-Schritt verwendete bereits
x̂ = αx + (1−α)z_alt, der u-Schritt aber das unrelaxierte x. Korrekt ist
u += x̂ − z; das Primalresiduum bleibt x − z. Für α=1 ist beides identisch,
der Fehler war deshalb in allen bisherigen Läufen wirkungslos.

**Adaptives ρ, neu definiert:** Residual Balancing auf den dimensionslosen
Quotienten der strikten Kriterien (max. über Ränge), da primales (p.u./rad)
und duales Residuum (Kosteneinheiten) nicht direkt vergleichbar sind.
ρ_v und ρ_θ ändern sich um denselben Faktor, u_neu = (ρ_alt/ρ_neu)·u_alt,
vollständige ρ-Historie im Ergebnis, Modelle werden mit Warmstart neu aufgebaut.
Fortsetzen adaptiver Läufe ist gesperrt.

| Variante, 3000 Iterationen | Primal max | Dual max / Median | max. P-Fehler | Kosten % | Ergebnis |
|---|---:|---:|---:|---:|---|
| Referenz: festes ρ=1e7, α=1 | 0.88 | 415 / 44 | 5.0e-4 | +1.30 | nicht konvergiert |
| adaptiv, Start 1e7, μ=10, τ=2, alle 50 It., [1e6, 1e8] | 395 | 641 / 65 | 0.86 | (unzulässig) | nicht konvergiert |
| Über-Relaxation α=1.6, ρ=1e7 | 1.71 | 572 / 60 | 2.4e-4 | +1.07 | nicht konvergiert |

- **Adaptiv:** Der duale Quotient ist anfangs rund 100-mal größer als der primale.
  Die Regel senkt ρ in 250 Iterationen bis an die Untergrenze 1e6, wo das Verfahren
  wie bereits bekannt pendelt. Klar schlechter als festes ρ=1e7.
- **Über-Relaxation:** glatterer Primalverlauf und kleinerer Bilanzfehler,
  aber beim maßgeblichen dualen Kriterium nicht besser als α=1.
- Keine Variante ist konvergiert oder AC-zulässig.

**Folgerung:** Mit festem, adaptivem oder relaxiertem einheitlichem ρ ist
118-busweises ADMM auf dem vollständigen Fall im praktikablen Budget nicht
konvergent. Das ist ein belegtes negatives Ergebnis. Die Clusterstudie stützt sich
deshalb zunächst auf Partitionen (3 Partitionen konvergiert und reproduziert);
118 Busagenten bleiben als Grenzfall dokumentiert. Kantengewichtetes ρ folgt
am Ende als getrenntes Modell.
