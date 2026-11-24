# Cluster-Runbook: vollständiges IEEE118-AC-OPF, zentral gegen verteilt

Gilt ab Stand September 2026 für diesen Branch. Ersetzt `legacy/docs/CLUSTER_AC_RUNBOOK.md`
und `legacy/docs/DEPLOYMENT_120_PIS.md` (historisch: DC-OPF bzw. NR-Polish, nicht mehr gültig).
Messregeln: `MEASUREMENT_PROTOCOL.md`. Lokale Referenz: `BENCHMARK_LOCAL_RESULTS.md`.

## 0. Rollen

- **pi119 = Orchestrator.** Startet alle Läufe (`mpirun`), hält das gemeinsame
  Laufverzeichnis, sammelt und wertet aus. **Er bekommt nie einen Rang** und rechnet
  keine Referenz.
- **Worker = pi1 … pi118 (und pi120)** rechnen ausschließlich: je Agent ein Rang auf
  einem eigenen Pi, 1 Gurobi-Thread. Die zentrale Referenz läuft ebenfalls auf einem
  Worker (Standard: pi1), damit zentral und verteilt auf derselben Hardwareklasse laufen.
- Worker führen keine Hintergrundjobs, Auswertungen oder Kopien aus.

## 1. Was gemessen wird

- Zentral: auf pi1, 1 Thread und alle Threads.
- Verteilt: rohes ADMM, ρ=3e5 (Abstimmungsprotokoll T11), `--centered-penalty`, `--partitioner connected_impedance`,
  k = 3, 4, 8, 16 Agenten auf den ersten k Workern.
- 118 busweise Agenten: mit ausgewiesener Variante `--edge-rho admittance` (lokal strikt
  konvergiert nach 61187 Iterationen, AC-zulässig; `validation/edge_rho_20260915/`).
- Lokal gemessene Iterationszahlen der Kampagnenkonfiguration (ρ=3e5, Toleranz 1e-4):
  633 / 1723 / 1746 / 3185, 118 Busagenten kantengewichtet 15219; strikt 1e-5: 936 / 3025 / 2955 / 5834.
  Abweichungen auf aarch64 sind möglich (Gleitkomma) und werden berichtet, nicht verworfen.
  Verworfen wird nur ein Lauf mit nachweislich falschem Software- oder Datenstand (Manifest-Hashes).

## 2. Vorbereitung (einmalig)

1. **Gemeinsames Laufverzeichnis:** pi119 exportiert per NFS z. B. `/srv/opf`, alle Worker
   mounten es unter **demselben Pfad** (`/srv/opf`). Repository und `results/` liegen dort.
   `tools/run_ac_local.py` legt Quellcode-Snapshot, Topologie, Hostfile und alle
   Rang-Ausgaben im Laufverzeichnis ab; ohne gemeinsamen Pfad starten Remote-Ränge nicht.
2. **Gleiche Umgebung auf allen Pis**, gleicher absoluter Python-Pfad
   (der Orchestrator startet Remote-Ränge mit seinem `sys.executable`):
   Python 3.11, Open MPI, mpi4py, numpy, PyYAML, Gurobi 13.x (aarch64).
3. **Gurobi-Lizenz** auf jedem Pi unter demselben Pfad (`GRB_LICENSE_FILE` wird
   weitergereicht). Lizenzdateien nie ins Repository.
4. Passwortloses SSH von pi119 zu allen Workern; Namensauflösung `piN`.
5. NTP-Zeitabgleich; Energiesparmodi und unnötige Dienste auf Workern aus.

## 3. Hostfiles erzeugen (auf pi119)

```bash
python tools/make_hostfiles.py cluster/hosts.txt --orchestrator pi119 \
  --out cluster/hostfiles --sizes 3 4 8 16 118
```

Schreibt `cluster/hostfiles/hosts_k<N>.txt` (erste N Worker nach Nummer, `slots=1`,
pi119 ausgeschlossen) und `hostfiles.json` mit fehlenden Hosts und Problemen.

**Aktueller Stand von `cluster/hosts.txt`:** pi103 und pi105 fehlen. Ohne pi119 stehen
117 Worker zur Verfügung (pi1–pi118 ohne 103/105, plus pi120). **k=118 mit einem Rang je
Pi ist damit nicht möglich**, bis pi103/pi105 ergänzt oder ein anderer Pi hinzugefügt wird.
Zwei Ränge auf einem Pi (`--slots-per-host 2`) verfälschen den Hardwarevergleich und
müssen gegebenenfalls ausdrücklich ausgewiesen werden.

## 4. Vorabprüfung (vor jeder Messsitzung, von pi119)

```bash
mpirun --hostfile cluster/hostfiles/hosts_k16.txt --map-by slot \
  -x GRB_LICENSE_FILE python tools/cluster_preflight.py --out preflight_k16.json
```

Nur bei `OK` weitermachen (Versionen, Gurobi-NLP mit Lizenz, identische Quell-/Topologie-
Hashes, MPI-Ring). Die JSON-Datei gehört zu den Messnachweisen.

## 5. Case Study ausführen (auf pi119, im gemeinsamen Verzeichnis)

Die gesamte Messkampagne ist in `cluster/case_studies.yaml` festgelegt (Fälle, Agentenzahlen,
Toleranz, Budgets, Wiederholungen) und wird mit `tools/run_campaign.py` ausgeführt:

| Fall | Inhalt | Wiederholungen | Toleranz | Budget |
|---|---|---:|---|---|
| C0_central | zentrale Referenz auf pi1, 1 Thread und alle Threads | 5 + 5 | – | – |
| C1_partitions | 3/4/8/16 Partitionen, `connected_impedance`, ρ=3e5 | 3 | 1e-4 | 1200 / 2500 / 2500 / 5000 |
| C2_bus118_edge_rho | 118 Busagenten, kantengewichtetes ρ₀=1e6 | 3 | 1e-4 | 20000 |
| C3_strict_tolerance | 3/4/8/16 Partitionen zum Vergleich, ρ=3e5 | 1 | 1e-5 | 1200 / 3800 / 3700 / 7300 |

```bash
python tools/make_hostfiles.py cluster/hosts.txt --orchestrator pi119 --out cluster/hostfiles --sizes 3 4 8 16 118
python tools/run_campaign.py --out results/cluster_YYYYMMDD --hostfiles cluster/hostfiles --central-host pi1 --dry-run
python tools/run_campaign.py --out results/cluster_YYYYMMDD --hostfiles cluster/hostfiles --central-host pi1
python tools/aggregate_benchmarks.py results/cluster_YYYYMMDD --out results/cluster_YYYYMMDD/aggregate
python tools/thesis_figures.py results/cluster_YYYYMMDD --out results/cluster_YYYYMMDD/figures
```

- `--dry-run` zeigt alle 29 Befehle; einzelne Fälle mit `--select C1_partitions`.
- Die Reihenfolge der Konfigurationen rotiert je Wiederholung. Fertige Läufe (mit `result.json`)
  werden übersprungen; nach einer Unterbrechung denselben Befehl erneut starten und einen
  abgebrochenen Laufordner vorher prüfen und entfernen.
- `campaign_manifest.json` hält Commit, Hash der Case-Study-Datei und alle Befehle fest,
  `campaign.log` Ergebnis und Dauer jedes Laufs.
- Der Starter wurde lokal mit `--local` getestet (dieselben Läufe ohne Hostfile).
- Vor C2: pi103 und pi105 in `cluster/hosts.txt` ergänzen (118 Worker nötig).
- Grobe Dauerabschätzung erst nach `tools/subproblem_benchmark.py` auf einem Pi möglich.

Einzelne Läufe manuell (entspricht einem Eintrag der Kampagne):

Zentral auf pi1, je Block 5 Wiederholungen je Thread-Einstellung:

```bash
python tools/run_ac_local.py --central --central-host pi1 --threads 1 --solve-limit 0 --timeout 600 \
  --out results/cluster_YYYYMMDD/central_t1_rX_Y
python tools/run_ac_local.py --central --central-host pi1 --threads 0 --solve-limit 0 --timeout 600 \
  --out results/cluster_YYYYMMDD/central_tall_rX_Y
```

Verteilt (Beispiel k=8), 3 Wiederholungen je k, Reihenfolge je Wiederholung rotieren:

```bash
# C1 (Kampagnentoleranz 1e-4, Budget aus cluster/case_studies.yaml)
python tools/run_ac_local.py --hostfile cluster/hostfiles/hosts_k8.txt --agents 8 --batch 8 \
  --centered-penalty --solve-limit 0 --rho 3e5 --partitioner connected_impedance \
  --abs-tol 1e-4 --rel-tol 1e-4 --iterations 2500 --timeout 7200 --threads 1 \
  --checkpoint-every 500 --log-every 0 --monitor-every 0 --timing-trace \
  --out results/cluster_YYYYMMDD/admm_p8_rX

# C3 (strikt 1e-5, eigenes Budget): --abs-tol 1e-5 --rel-tol 1e-5 --iterations 3700
```

Die Flaggen entsprechen `cluster/case_studies.yaml`; maßgeblich ist immer die Kampagnendatei,
`tools/run_campaign.py --dry-run` zeigt die exakten Befehle. Ohne `--abs-tol/--rel-tol` gilt die
strikte Vorgabe 1e-5, die mit den C1-Budgets nicht erreicht wird.

Regeln:
- **Kein `--scratch`** (nicht gemeinsam sichtbar) und **kein `--mpi-yield`**. Lokal ist
  `--scratch` dagegen vorgeschrieben (`docs/MEASUREMENT_PROTOCOL.md` §2).
- Alle Experimentvariablen werden per `-x` an die Ränge weitergereicht und im Manifest
  gespeichert; das Manifest nennt auch Hostfile, Anzahl Hosts/Slots und `launcher_host`.
- Jobzeit wird auf pi119 gemessen und enthält den `mpirun`-Start auf den Workern.
  Lokal kostete das bei der zentralen Referenz rund 0.9 s zusätzlich; für den Vergleich
  zusätzlich Kernzeiten (Gurobi bzw. ADMM-Schleife) berichten.

## 5a. Iterationsbudget

Aus den lokalen Checkpoint-Verläufen (`tools/iteration_budget.py`,
`validation/iteration_budget_20260915/`). Meilenstein „Qualität“: AC-zulässig **und**
Kosten innerhalb ±1 % der zentralen Referenz (Checkpoint-Auflösung 500–1000 Iterationen).
Iterationen sind bei gleicher Software deterministisch; der Zuschlag von 25 % deckt
Gleitkommaunterschiede der ARM-Plattform ab.

| Konfiguration (flacher Start) | zulässig und ±1 % ab | zulässig und ±0,1 % ab | strikt konvergiert | Budget Qualität | Budget strikt |
|---|---:|---:|---:|---:|---:|
| 3 Partitionen, ρ=1e6 | ≤ 1000 | ≤ 1000 | 3124 | 1500 | 4000 |
| 4 Partitionen, ρ=1e6 | ≤ 1500 | ≤ 1500 | 10013 | 2000 | 13000 |
| 8 Partitionen, ρ=1e6 | ≤ 1500 | ≤ 1500 | 9917 | 2000 | 13000 |
| 16 Partitionen, ρ=1e6 | ≤ 2500 | ≤ 2500 | 19374 | 3500 | 25000 |
| 118 Busagenten, kantengew. ρ₀=1e6 | ≤ 9000 | ≤ 11000 | 61187 | 11500 | 77000 |

Begrenzend ist die AC-Zulässigkeit (Bilanz ≤ 1e-4 p.u.), nicht die Kostenabweichung.

Die Tabelle zeigt die Vorserie bei ρ=1e6. Für die Partitionen gilt in der Kampagne ρ=3e5
(strikt 936 / 3025 / 2955 / 5834); die Budgets in `cluster/case_studies.yaml` sind daraus
abgeleitet (`docs/TOLERANCE_BUDGET.md`).

**ADMM-Toleranzstufen.** Werden ε_abs und ε_rel gemeinsam um den Faktor c gelockert, sinkt
jeder Quotient Residuum/Toleranz genau um 1/c. Aus den Checkpoints (Auflösung 500–2000
Iterationen) ergibt sich, wann alle Ränge die Stufe erfüllen, und ob der Zustand dort
unabhängig AC-zulässig ist:

| Konfiguration | Toleranz 1e-3 | Toleranz 1e-4 | Toleranz 3e-5 | strikt 1e-5 |
|---|---|---|---|---|
| 3 Partitionen | ≤ 1500, zulässig | ≤ 2500, zulässig | ≤ 3000, zulässig | 3124 |
| 4 Partitionen | ≤ 1500, zulässig | ≤ 6000, zulässig | ≤ 8000, zulässig | 10013 |
| 8 Partitionen | ≤ 2000, zulässig | ≤ 6000, zulässig | ≤ 8000, zulässig | 9917 |
| 16 Partitionen | ≤ 2000, **unzulässig** | ≤ 11000, zulässig | ≤ 15500, zulässig | 19374 |
| 118 Busagenten, kantengew. | ≤ 2000, **unzulässig** | ≤ 16000, zulässig (+0,05 %) | nicht erreicht bis 30000 | 61187 |

Folgerung: **1e-4 ist die lockerste Toleranz, bei der in allen Konfigurationen der Zustand
zulässig ist** (Kostenabweichung ≤ 0,06 %). 1e-3 reicht für 16 und 118 Agenten nicht.
Wird für die Kampagne 1e-4 gewählt (`--abs-tol 1e-4 --rel-tol 1e-4`), ist das
vorab festzulegen und im Bericht als gewählte Toleranz auszuweisen; die unabhängige
AC-Prüfung bleibt Abnahmebedingung, und 1e-5 wird wo möglich zusätzlich berichtet.
Für die Messkampagne: Läufe mit dem strikten Budget starten (`--iterations`); die Zeit bis
zum Qualitätsmeilenstein wird aus dem Residuenverlauf und Checkpoints ausgewertet
(`--checkpoint-every 500` für diese Auswertung setzen). Ein Lauf, der das strikte Budget
erreicht, ohne zu konvergieren, gilt als nicht konvergiert und wird so berichtet.

## 6. Auswertung (auf pi119)

```bash
python tools/aggregate_benchmarks.py results/cluster_YYYYMMDD --out results/cluster_YYYYMMDD/aggregate
```

Akzeptiert werden nur Läufe mit strikter Konvergenz, unabhängiger AC-Prüfung und
bitgleichen Endzuständen über Wiederholungen. Iterationszahlen gegen die lokale
Referenz prüfen.

## 7. Lokal getestet

- `--hostfile` mit einem lokalen Host (3 Slots): Iterate bitgleich zum Referenzlauf.
- `--central-host localhost`: gleiche Kosten, Gurobi-Zeit unverändert.
- `tools/cluster_preflight.py` mit 3 Rängen.
- `tools/make_hostfiles.py` auf `cluster/hosts.txt`.

Nicht lokal testbar: echter Mehrrechnerbetrieb, NFS, SSH-Start, Netzwerklatenz.
