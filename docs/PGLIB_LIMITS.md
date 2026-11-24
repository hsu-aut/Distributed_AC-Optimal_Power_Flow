# Zweiggrenzen: Variante PGLib-OPF case118

Stand 15.09.2026. Ergänzung zu Arbeitsschritt 4 („Einhaltung von Spannungsbändern und
Zweiggrenzen“). Der Studienfall MATPOWER 8.1 `case118` hat keine thermischen Grenzen
(RATE_A = 0); Zweiggrenzen sind dort trivial eingehalten. Deshalb wird zusätzlich eine
**ausgewiesene Variante** mit Grenzen aus einer dokumentierten Quelle gerechnet. Die
Hauptkampagne bleibt beim Originalfall. Nachweise: `validation/pglib_limits_20260915/`.

## Datensatz

- Quelle: IEEE PES Power Grid Library – Optimal Power Flow v23.07, `pglib_opf_case118_ieee.m`
  (CC BY 4.0), unverändert unter `data/pglib/`, SHA256
  `b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78`.
- Import mit demselben Importer wie der Originalfall: `topology_ieee118_pglib.yaml`.
- Gleiche Netzdaten wie MATPOWER `case118` (r, x, b, Taps, Lasten, Spannungsgrenzen).
  Unterschiede: thermische Grenzen für alle 186 Zweige (0.72–72.2 p.u.), Winkeldifferenzgrenzen
  ±30°, geänderte Kostenkoeffizienten und P_max aller 54 Generatoren, geänderte Q-Grenzen an
  12 Generatoren. Kosten sind daher **nicht** mit dem Originalfall vergleichbar.

## Ergebnisse (ρ=1e6, `connected_impedance`, strikte Toleranz 1e-5)

| Lauf | Iterationen | akzeptiert | Kosten | Abweichung | max. P-/Q-Fehler p.u. | max. therm. Verletzung | max. Auslastung |
|---|---:|---|---:|---:|---|---:|---:|
| zentral (Gurobi, 1 Thread) | – | ja | 97213.6074 | – | – | 0 | 100.0 % |
| 3 Partitionen | 2265 | ja | 97213.6054 | −0.000002 % | 1.6e-7 / 9.1e-8 | 0 | 100.0 % |
| 8 Partitionen | 8431 | ja | 97213.6077 | +0.0000004 % | 2.4e-7 / 2.4e-7 | 2.2e-8 p.u. | 100.0 % |

Aktive Grenzen (`tools/limits_utilization.py`, Bindungstoleranz 1e-4 p.u.) sind in allen drei
Lösungen identisch: 11 Busse an V_max, 45 Generatoren an P_max, 40 an P_min, 20 an Q_max, 3 an
Q_min, **2 Zweigenden an der thermischen Grenze** (Zweig 105 an Bus 68, Zweig 162 an Bus 99).
Größte Winkeldifferenz 15.8° (Grenze 30°). Abweichung vom zentralen Arbeitspunkt: max. ΔV
1.7e-4 p.u., max. ΔP_g 0.37 MW.

## Befund

Mit aktiven thermischen Grenzen konvergiert die verteilte Formulierung unverändert und trifft
dieselben aktiven Grenzen wie die zentrale Lösung; die thermische Verletzung liegt mit 2.2e-8
p.u. weit unter der Prüftoleranz 1e-5. Die zusätzliche Bindung verändert die Iterationszahl
(3 Partitionen 2265 statt 3124, 8 Partitionen 8431 statt 9917 im Originalfall), ist aber wegen
der anderen Kosten kein direkter Vergleich.

Nicht untersucht: 4/16 Partitionen und 118 Busagenten in dieser Variante.
