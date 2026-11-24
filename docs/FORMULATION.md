# Formulierung der lokalen AC-Teilprobleme

Methodendokument zur Studienarbeit. Beschreibt, was implementiert ist, welche Varianten
untersucht wurden und nach welchen Kriterien ausgewählt wird. Belege stehen jeweils in
`validation/` und den Ergebnisdokumenten unter `docs/`.

## 1. Zentrales Problem

IEEE-118-Bus-System, unveränderter MATPOWER-8.1-Fall (`data/matpower/case118.m`,
importiert als `topology_ieee118_full.yaml`). Variablen je Bus i: Spannungsbetrag V_i,
Winkel θ_i (Referenzbus θ=0, Betrag frei), Generatorleistungen P_g, Q_g.

- Ziel: Summe der quadratischen Erzeugungskosten (Originalkoeffizienten).
- Zweigflüsse an beiden Enden im π-Modell mit Tap und Phasenschieber als exakte
  nichtlineare Gleichungen (`distributed_opf/pwl_ac.py`, im Vollmodus native `nlfunc.sin/cos`).
- Harte P- und Q-Bilanz je Bus inklusive Busshunts.
- Harte Grenzen: Spannungsband, P_g/Q_g, vorhandene Winkeldifferenz- und Scheinleistungsgrenzen.
  Im MATPOWER-Fall sind alle RATE_A null, es gibt also keine thermischen Zweiggrenzen;
  es werden keine erfunden.

Das Problem ist nichtkonvex. Gurobi 13 löst lokal (`OptimalityTarget=1`, Status 18
„lokal optimal“); ein globales Optimalitätszertifikat liegt nicht vor (Einordnung über die
numerisch bestimmte SOCP-Relaxationslücke von 0.25 %, Abschnitt 9). Die zentrale
Referenz (`central_ac_opf.py`) verwendet dieselben Gleichungen; eine unabhängige
PYPOWER-Rechnung bestätigt die Kosten bis auf 3e-9 relativ.

## 2. Zerlegung und Kopplungsgrößen

**Agenten.** Jeder Agent besitzt eine Menge von Bussen (knotenscharf: ein Bus; Subnetz:
mehrere zusammenhängende Busse). Ein Zweig zwischen zwei Agenten ist eine *Schnittkante*.

**Kopplungsgrößen.** An jeder Schnittkante (i, j) werden **Spannungsbetrag und Winkel**
beider Endbusse abgestimmt. Jeder Agent hält lokale Kopien V_j^(i), θ_j^(i) der
Nachbarbusse und berechnet den Fluss über die Schnittkante selbst aus eigenen Größen und
Kopien. Wirk- und Blindleistungsflüsse werden **nicht** als Kopplungsgrößen verwendet:
Mit V und θ an beiden Enden sind die Flüsse beider Enden (inklusive Verlusten) eindeutig
bestimmt, sodass die Bilanz jedes Busses lokal exakt formulierbar bleibt.

Je Schnittkante entstehen vier Konsensgruppen mit je zwei Kopien:
V_j (eigener Wert beim Besitzer, Kopie beim Nachbarn), θ_j, V_i, θ_i.

## 3. Lokales Teilproblem und ADMM-Schritte

Skalierte Form des Konsens-ADMM (Boyd et al. 2011) mit Dualvariablen u und Konsenswert z
je Kopie.

- **x-Schritt (lokal, parallel):** Jeder Agent löst sein vollständiges nichtlineares
  AC-Teilproblem (eigene Busse, eigene Zweige, Randzweige mit Kopien), Zielfunktion
  = eigene Erzeugungskosten + Σ ½ ρ_e ‖x − z + u‖² über alle eigenen Kopien.
  Harte Bilanzen und Grenzen bleiben im Teilproblem; es gibt keine Slack-Variablen.
- **z-Schritt:** Mittelwert der beiden Kopien (plus u) je Konsensgruppe; beide
  Endpunkte berechnen denselben Wert.
- **u-Schritt:** u += x̂ − z mit x̂ = αx + (1−α)z_alt (α=1: Standard-ADMM).
- **Kommunikation:** Konsensaustausch nur mit Nachbarrängen, ein Austausch je Iteration mit
  8 float64 je Schnittkante (`distributed_opf/neighbor_exchange.py`). Zusätzlich je Iteration zwei
  globale Reduktionen für die Abbruchprüfung und im busweisen Modus eine für den Solverstatus.

**Zentrierte Strafdarstellung** (`--centered-penalty`): algebraisch identische Umformung
½w x² + c x = ½w (x + c/w)² − c²/(2w) mit Abweichungsvariablen. Nötig, weil bei großem ρ
die direkte Form zu numerischen Abbrüchen der lokalen Löser führte. Das Verfahren bleibt
unverändert.

## 4. Abbruchkriterien und Zulässigkeitsprüfung

Ein Lauf gilt nur als erfolgreich, wenn **alle** Bedingungen erfüllt sind:

1. jede lokale Lösung mit Status optimal/lokal optimal (kein Zeitlimit-Zwischenstand),
2. das Abbruchkriterium des Lösers auf **jedem** Rang (Konvention unten, Abschnitt 4.2),
3. unabhängige AC-Prüfung des zusammengesetzten Zustands (`distributed_opf/ac_validation.py`):
   aus V, θ, P_g, Q_g werden alle Zweigströme komplex neu berechnet; P-/Q-Bilanz ≤ 1e-4 p.u.,
   alle Grenzen ≤ 1e-5. Keine Newton-Raphson- oder Lastfluss-Nachbearbeitung.

Zusätzlich protokolliert (nicht entscheidungsrelevant): der Residuenverlauf jeder Iteration
(`logs/residual_trace.npy`) mit dem Löserkriterium und der Boyd-Matrixform je Rang und global.

### 4.1. Kopplungsstruktur in Matrixform

Gekoppelte Variablen je Rang: eigene Spannung und eigener Winkel jedes Grenzbusses sowie je eine
lokale Kopie von V und θ jedes fremden Nachbarbusses. Je Schnittkante e = {i, j} und Endpunkt
b ∈ {i, j} gibt es zwei Konsensgruppen g (V und θ) mit Konsenswert z_g und zwei **Mitgliedern**:
der eigenen Variable beim Besitzer von b und der Kopie beim anderen Rang. Eine eigene Variable
gehört zu so vielen Gruppen, wie ihr Bus Schnittkanten hat; eine Kopie zu so vielen, wie lokale
Busse an denselben fremden Bus grenzen. Mit A (wählt je Mitglied seine Variable), E (wählt je
Mitglied seine Gruppe) und R = diag(ρ_g) (einheitlich oder kantengewichtet) lautet die Kopplung
Ax − Ez = 0, und nach Boyd et al. (2011, Gl. 3.11–3.12):

    r = Ax − Ez,                 s = Aᵀ R E (z − z_alt)
    ε_pri  = √p ε_abs + ε_rel max(‖Ax‖, ‖Ez‖)        p = Anzahl Mitglieder
    ε_dual = √n ε_abs + ε_rel ‖Aᵀ R u‖               n = Anzahl gekoppelter Variablen

Im dualen Residuum werden die Beiträge aller Gruppen einer Variable **vor** dem Quadrieren
summiert (Aᵀ). Alle Größen zerfallen exakt in Summen über Ränge, weil jede Variable und jedes
Mitglied genau einem Rang gehört. Umsetzung: `distributed_opf/consensus_residuals.py`, Test
`tests/test_consensus_residuals.py`.

### 4.2. Konvention des Lösers und Einordnung

Das Kriterium, das die Läufe tatsächlich beendet (`agent_ac_mpi._check_convergence_local`,
eingefroren, damit alle bisherigen Iterationszahlen gültig bleiben), weicht von dieser Form ab:

| Größe | Löser je Rang | Boyd-Matrixform |
|---|---|---|
| primales Residuum | ‖Ax − Ez‖ (identisch) | ‖Ax − Ez‖ |
| duales Abbruchmaß | √(Σ_Mitglieder ρ_g² Δz_g²), ohne Aᵀ-Zusammenfassung | ‖Aᵀ R E Δz‖ |
| ε_pri | √p ε_abs + ε_rel max(‖V, θ aller Busse der Partition sowie Kopien‖, ‖Ez‖), zusätzlich ‖r‖ ≤ ε_abs | √p ε_abs + ε_rel max(‖Ax‖, ‖Ez‖) |
| ε_dual | √p ε_abs + ε_rel √(Σ_Mitglieder ρ_g² u²) | √n ε_abs + ε_rel ‖Aᵀ R u‖ |

Weil die Aᵀ-Zusammenfassung fehlt, ist die duale Größe des Lösers die elementweise, mit ρ
skalierte Änderung der Konsensvariablen und **nicht** das kanonische duale Residuum. Abbildungen
und Berichte benennen sie deshalb als *duales Änderungsmaß* ‖ρ Δz‖ und nicht als duales Residuum.

Nach Cauchy-Schwarz ist das Boyd-Residuum höchstens um den Faktor √d größer als das des Lösers
(d = größte Zahl an Gruppen je Variable); durch Vorzeichenausgleich zwischen den Gruppen kann es
aber auch deutlich kleiner sein. ε_dual unterscheidet sich zusätzlich in √n statt √p und in der
Aᵀ-Zusammenfassung von u. Ein allgemeiner Größenvergleich der beiden Kriterien folgt daraus
nicht; für die untersuchten Konfigurationen liegen die Abbruchzeitpunkte nahe beieinander
(`docs/CRITERION_COMPARISON.md`). Das primale Kriterium des Lösers enthält zusätzlich die
absolute Schranke ‖r‖ ≤ ε_abs. Beide Kriterien werden in jedem neuen Lauf
nebeneinander aufgezeichnet; der Vergleich für die Kampagnenkonfigurationen steht in
`docs/CRITERION_COMPARISON.md`. Die Abnahme eines Laufs verlangt unabhängig davon immer die
AC-Prüfung des zusammengesetzten Zustands.

## 5. Partitionierung

| Strategie | Beschreibung | Befund |
|---|---|---|
| knotenscharf (118 Agenten) | ein Bus je Agent, alle 186 Zweige Schnittkanten | mit einheitlichem ρ nicht konvergent (`docs/PHASE2_RESULTS.md`) |
| `graph_aware` (früher) | Breitensuche mit Auffüllen nach Bus-Nummer | erzeugt nicht zusammenhängende Partitionen und schneidet den steifsten Zweig 67–115 (\|y\|=246 p.u.); 4/8/16 unzulässig |
| `connected_impedance` | zusammenhängend, Blattbusse und steife Zweige intern, Größenband ±20 %, minimale geschnittene Admittanz | 3/4/8/16 strikt konvergiert, reproduzierbar (`docs/PHASE5_RESULTS.md`) |

Begründung: Eine Winkelabweichung Δθ an einer Schnittkante erzeugt einen Bilanzfehler von
etwa \|y_e\|·Δθ. Steife Schnittkanten verlangen daher sehr kleine Konsensfehler, schwache
Kanten lassen Oszillationen zu. Wenige, schwache Schnittkanten erleichtern die Konvergenz.

## 6. Strafparameter und Varianten

| Variante | Schalter | Ergebnis |
|---|---|---|
| einheitliches festes ρ (Standard) | `--rho` | Partitionen: ρ=1e6 konvergiert; 118 Agenten: 1e6 pendelt, ≥3e7 duale Konvergenz zu langsam |
| getrenntes ρ_V / ρ_θ | `--rho-v`, `--rho-theta` | keine Konvergenz bei 118; großes ρ_θ ungünstig |
| adaptives ρ (Residual Balancing, normierte Quotienten) | `--adaptive-rho` | fällt an die Untergrenze, pendelt; verworfen |
| Über-Relaxation | `--relaxation` | glatter, aber duale Konvergenz nicht besser |
| **kantengewichtetes ρ** (eigenes Modell) | `--edge-rho admittance` | ρ_e = ρ₀·(\|y_e\|/Median\|y\|)^p; bei 118 Agenten mit p=1, ρ₀=1e6 AC-zulässig ab ~10 000 Iterationen, strikte Konvergenz in Untersuchung |

Kantengewichtetes ρ ist ADMM mit fester diagonaler Strafmatrix: beide Kopien einer
Konsensgruppe teilen ρ_e, der z-Schritt bleibt der einfache Mittelwert, x-Schritt-Strafe,
duales Residuum und Toleranz verwenden ρ_e. Mit p=0 ist es bitgleich zum Standard.

## 7. Initialisierung und lokale Optima

Standard ist ein **flacher Start**: V=1, θ=0, P_g=Q_g=0, z_V=1, z_θ=0, u=0. Jede lokale
Lösung startet warm aus der vorherigen lokalen Lösung; der vollständige Startvektor wird
im Checkpoint gesichert, sodass Fortsetzungen bitgleich zu ununterbrochenen Läufen sind.

Da das Problem nichtkonvex ist, können zentrale und verteilte Lösung in unterschiedlichen
lokalen Optima landen. Untersucht wurde das auf zwei Ebenen:

**Zentrale Referenz** (`validation/central_multistart_20260915/`): 16 Starts – Standardstart,
flacher Start, PYPOWER-Lösung, PYPOWER-Lösung um 5 % und 20 % gestört (je 5 Seeds), flacher
Start um 20 % gestört (3 Seeds) – erreichen alle dasselbe lokale Optimum (Kostenspanne 4e-6,
max. Spannungsabweichung 7e-9 p.u.). Die Referenz ist damit robust gegenüber dem Startpunkt;
ein globales Zertifikat ist das nicht.

**Verteiltes ADMM** (`results/init_study_20260915/`, 3 und 8 Partitionen, ρ=1e6):

| Start | 3 Partitionen | 8 Partitionen |
|---|---:|---:|
| flach (Standard) | 3124 | 9917 |
| zentrale Lösung | 378 | 982 |
| zentrale Lösung, 2 % gestört, Seed 1 / 2 | 1646 / 1551 | 2886 / 1935 |

Alle Läufe sind strikt konvergiert und unabhängig AC-zulässig, Kosten innerhalb 1e-5 %, und
treffen dieselben aktiven Grenzen wie die zentrale Lösung. Der Startpunkt beeinflusst damit
die Iterationszahl stark, in den untersuchten Fällen aber nicht das erreichte Optimum.
Ein Start aus der zentralen Lösung ist im verteilten Betrieb nicht verfügbar und dient nur der
Einordnung; die Kampagne verwendet den flachen Start.

## 8. Auswahlkriterien

Die Formulierung wird nach folgenden, aus der Aufgabenstellung abgeleiteten Kriterien
bewertet, in dieser Rangfolge:

1. **Physikalische Zulässigkeit** des zusammengesetzten Arbeitspunkts ohne Nachbearbeitung.
2. **Strikte Konvergenz** auf allen Rängen in praktikablem Iterationsbudget, reproduzierbar.
3. **Lösungsgüte**: Kostenabweichung zur zentralen Referenz.
4. **Verteilbarkeit**: knotenscharf möglich (118 Recheneinheiten), Konsensaustausch nur mit
   Nachbarn (globale Reduktionen nur für Abbruchprüfung und Solverstatus),
   kleine Kopplungsnachrichten.
5. **Aufwand**: Iterationen, Zeit je Iteration und deren Zerlegung in lokale Optimierung,
   Kommunikation und Overhead.

Konvexe Relaxierungen (SOCP/SDP) wurden bewusst nicht als lokales Teilproblem gewählt,
weil ihre Lösung ohne Exaktheitsnachweis keinen zulässigen AC-Arbeitspunkt garantiert und
Kriterium 1 dann eine Nachbearbeitung erfordern würde.

## 9. Umgang mit der Nichtkonvexität: Einordnung der Lösungswege

Die Nichtkonvexität des AC-OPF stammt aus den bilinearen bzw. trigonometrischen
Flussgleichungen und aus der unteren Spannungsgrenze. Folgen: mehrere lokale Optima möglich,
Ergebnis abhängig vom Startpunkt, keine allgemeine Konvergenzgarantie für zerlegte Verfahren.
Gängige Umgangsweisen (Überblick Capitanescu 2016; Low 2014; Molzahn et al. 2017) und ihre
Rolle in dieser Arbeit:

| Weg | Idee | Aussage über das Ergebnis | Rolle hier |
|---|---|---|---|
| Lokaler NLP-Löser (Innere-Punkte-Verfahren) | exakte Gleichungen, KKT-Punkt | zulässig und lokal optimal, kein globales Zertifikat | **gewählt**: zentral und in jedem ADMM-Teilproblem (Gurobi NL, PYPOWER/PIPS zur Gegenprüfung) |
| Konvexe Relaxierung (SOCP nach Jabr, SDP nach Lavaei/Low) | Produkte der Spannungen durch Hilfsvariablen ersetzen, Rang-/Zyklusbedingung fallen lassen | exaktes Optimum ist eine globale Unterschranke (numerisch nur näherungsweise); Lösung nur bei Exaktheit AC-zulässig | **Einordnung der Lösungsgüte** (s. u.); als Teilproblem verworfen (Kriterium 1, Abschnitt 8) |
| Approximation (DC-OPF, stückweise linear) | Linearisierung um |V|=1 bzw. Stützstellen | weder zulässig noch Schranke im strengen Sinn | ausgeschlossen; DC nur als Vergleich (`docs/DC_COMPARISON.md`) |
| Globale Verfahren (räumliches Branch-and-Bound) | Relaxierung plus Verzweigung | globales Optimum mit Zertifikat | für 118 Busse verteilt nicht praktikabel; nicht untersucht |

**Verteilte Verfahren.** ADMM (Boyd et al. 2011) zerlegt am Konsens der Kopplungsgrößen; für
nichtkonvexe Probleme existieren Konvergenzaussagen nur unter Annahmen (z. B. Lipschitz-
Glattheit und hinreichend großes ρ, Wang/Yin/Zeng 2019), die für den AC-OPF mit harten
Gleichungen nicht nachgewiesen sind. Konvergenz wird deshalb empirisch mit strikten
Residuenkriterien und unabhängiger AC-Prüfung belegt (Abschnitt 4). ALADIN (Engelmann et al.
2019) bietet lokale Konvergenzgarantien, benötigt aber einen zentralen Koordinationsschritt
mit Sensitivitätsinformation und widerspricht damit der reinen Nachbarkommunikation
(Kriterium 4); es wird als Alternative diskutiert, nicht implementiert.

**Einordnung der Lösungsgüte.** Die Jabr-SOCP-Relaxierung (`tools/socp_lower_bound.py`, Gurobi
Status OPTIMAL, Barrier ohne Crossover) liefert numerisch:

| Fall | Relaxationswert (numerisch) | zentrale AC-Lösung | Relaxationslücke |
|---|---:|---:|---:|
| Original case118 | 129339.54 | 129660.69 | 0.248 % |
| PGLib case118 | 96334.66 | 97213.61 | 0.904 % |

Das exakte Optimum der Relaxierung wäre eine Unterschranke des globalen Optimums. Der vom Löser
gelieferte primale Zielwert ist aber nur eine numerische Näherung, eine validierte duale Schranke
liegt nicht vor; die Werte sind deshalb eine numerisch bestimmte Relaxationslücke, kein
zertifizierter Abstand zum globalen Optimum. Die Lücke zeigt auch nicht, dass die Relaxierung
inexakt ist: Die AC-Lösung ist nur lokal optimal, eine positive Lücke kann auch von ihr herrühren.
Zusammen mit den 16 zentralen Starts, der PYPOWER-Gegenrechnung und den identischen aktiven
Grenzen aller verteilten Lösungen stützt der kleine Wert die Aussage, dass die verteilten
Lösungen das zentrale lokale Optimum treffen und ein deutlich günstigeres Optimum
unwahrscheinlich ist; bewiesen ist das nicht. Nachweise:
`validation/socp_lower_bound_20260915/`, `validation/central_multistart_20260915/`.

