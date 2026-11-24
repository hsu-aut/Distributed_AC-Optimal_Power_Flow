# Dauerabschätzung der Messkampagne (Vorlage, im Labor ausfüllen)

Die Iterationszahlen sind hardwareunabhängig (lokal gemessen, `cluster/case_studies.yaml`).
Die Zeit je Iteration hängt vom Pi und vom Netzwerk ab und wird am ersten Labortag bestimmt.

## 1. Messung am ersten Labortag

1. `tools/subproblem_benchmark.py --agents 1 3 8 16 118 --repeats 5 --max-partitions 4 --out results/pi_subproblem`
   auf **einem** Worker-Pi → t_solve(n) je Teilproblemgröße.
2. Kurzlauf je Konfiguration mit 200 Iterationen über `tools/run_ac_local.py --hostfile ... --iterations 200`
   → gemessene ms je Iteration aus `admm_run_summary.json` (`wall_iterations_s / wall_iterations`).
3. Zentrale Referenz einmal auf pi1 → Jobzeit.

## 2. Hochrechnung

    Dauer_Lauf ≈ Iterationen · ms_je_Iteration + Start (Modellaufbau, ~30–120 s auf Pis schätzen)
    Dauer_Fall ≈ Wiederholungen · Σ Dauer_Lauf

| Fall | Konfiguration | Iterationen (lokal, Toleranz) | ms/Iteration Pi | Dauer je Lauf | Wiederholungen | Dauer gesamt |
|---|---|---:|---:|---:|---:|---:|
| C0 | zentral 1 Thread / alle | – | – | ___ s | 5 + 5 | ___ |
| C1 | 3 Partitionen, ρ=3e5 | 633 (1e-4) | ___ | ___ | 3 | ___ |
| C1 | 4 Partitionen, ρ=3e5 | 1723 (1e-4) | ___ | ___ | 3 | ___ |
| C1 | 8 Partitionen, ρ=3e5 | 1746 (1e-4) | ___ | ___ | 3 | ___ |
| C1 | 16 Partitionen, ρ=3e5 | 3185 (1e-4) | ___ | ___ | 3 | ___ |
| C2 | 118 Busagenten, kantengew. ρ | 15219 (1e-4) | ___ | ___ | 3 | ___ |
| C3 | 3/4/8/16 strikt, ρ=3e5 | 936 / 3025 / 2955 / 5834 (1e-5) | ___ | ___ | 1 | ___ |

Iterationen bei 1e-4 sind obere Schranken aus Checkpoints; sie werden durch die lokale
Kampagne (`results/local_campaign_tol1e4_20260915/`) exakt bestimmt und hier nachgetragen.

## 3. Entscheidungsregel

- Passt die Gesamtdauer nicht in die verfügbare Laborzeit, zuerst die Wiederholungen von C3
  und die zweite Thread-Variante von C0 streichen, dann C2 auf 2 Wiederholungen reduzieren.
- Änderungen an Fällen, Budgets oder Toleranz nur vor Beginn und mit Vermerk in
  `cluster/case_studies.yaml` (Commit), nie während der Kampagne.
