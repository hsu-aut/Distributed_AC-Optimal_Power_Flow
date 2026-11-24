# Gegenprobe der eingefrorenen Kampagnenkonfiguration an der PGLib-Variante

Stand 15.09.2026. Nachweis `validation/pglib_holdout_20260915/`.

## Zweck

ρ = 3e5, Toleranz 1e-4 und die Budgets wurden am Originalfall `case118` abgestimmt
(`docs/TUNING_PROTOCOL.md`). Um eine Überanpassung an diesen Fall zu prüfen, wird dieselbe
Konfiguration **unverändert** auf die PGLib-Variante (`topology_ieee118_pglib.yaml`: andere
Kosten, Erzeugungs- und Blindleistungsgrenzen, thermische Grenzen, ±30°) angewendet.
Referenz ist die zentrale PGLib-Lösung 97213.6074. Kein Parameter wurde nach Sichtung dieser
Ergebnisse geändert.

## Ergebnisse

Partitionierer `connected_impedance`, flacher Start, Budgets wie `cluster/case_studies.yaml`
(C1: 1200 / 2500 / 2500 / 5000; strikt: 10000).

| Partitionen | Toleranz | Iterationen | Kosten gegenüber zentral | max. ΔP / ΔQ (Grenze 1e-4) | max. therm. Verletzung (Grenze 1e-5) |
|---|---|---:|---:|---:|---:|
| 3 | 1e-4 | 452 | −0.00016 % | 1.4e-5 / 3.9e-6 | 0 |
| 4 | 1e-4 | 1237 | +0.00010 % | 4.8e-5 / 5.3e-5 | < 1e-5 |
| 8 | 1e-4 | 1431 | +0.00012 % | 9.2e-5 / **9.97e-5** | 8.9e-6 |
| 16 | 1e-4 | 2457 | −0.00222 % | **9.8e-5** / 3.3e-5 | 8.6e-6 |
| 3 | 1e-5 | 675 | −0.00003 % | 2.3e-6 / 8.0e-7 | 0 |
| 4 | 1e-5 | 1857 | −0.00002 % | 5.5e-6 / 5.6e-6 | 0 |
| 8 | 1e-5 | 2381 | −0.00001 % | 3.2e-6 / 3.1e-6 | 2.9e-7 |
| 16 | 1e-5 | 3550 | −0.00019 % | 1.4e-5 / 3.8e-6 | 1.5e-6 |

Alle acht Läufe sind akzeptiert. Die Läufe bei 1e-4 treffen dieselben aktiven Grenzen wie die
zentrale Lösung (11 an V_max, 45 P_max, 40 P_min, 20 Q_max, 3 Q_min, 2 thermisch ausgelastete
Zweige; `limits_tol1e4/`).

## Befund

- **Übertragbarkeit:** Die am Originalfall gewählte Konfiguration konvergiert ohne Anpassung
  auch auf einem Fall mit anderen Kosten und aktiven Zweiggrenzen, innerhalb der Budgets.
- **Reserve bei 1e-4 ist klein:** Bei 8 und 16 Partitionen liegen die Bilanzfehler nur 0.3 %
  bzw. 2 % unter der Abnahmegrenze, die thermische Verletzung knapp unter 1e-5 (Originalfall:
  Faktor 2–5 Reserve). 1e-4 ist damit die lockerste noch tragfähige Stufe, nicht eine mit
  komfortablem Abstand. Bei strikter Toleranz 1e-5 beträgt die Reserve mindestens Faktor 7.
- **Folgerung für die Kampagne:** Kampagnentoleranz bleibt 1e-4 (eingefroren), die strikte
  Vergleichsserie C3 ist verpflichtend und wird nicht gestrichen. Ein Lauf, der bei 1e-4 die
  AC-Prüfung knapp verfehlt, gilt als nicht akzeptiert und wird so berichtet.

## Speicherbedarf je Rang

Gemessen als RSS der Rangprozesse (alle 2 s, x86-64, Gurobi 13, 1 Thread): 85–89 MB je Rang,
unabhängig von der Teilproblemgröße (3 bis 118 Agenten). Auf einem Raspberry Pi 5 läuft je
Worker ein Rang; der Speicherbedarf ist damit unkritisch. Der aarch64-Wert wird im Labor mit
dem Preflight bestätigt.
