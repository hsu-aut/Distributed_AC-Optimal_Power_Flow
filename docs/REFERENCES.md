# Literatur (Arbeitsliste)

Bibliografische Angaben vor der Übernahme in die Arbeit am Original prüfen
(Seitenzahlen, Jahrgang, DOI).

## ADMM und verteilte Optimierung
- S. Boyd, N. Parikh, E. Chu, B. Peleato, J. Eckstein: *Distributed Optimization and Statistical
  Learning via the Alternating Direction Method of Multipliers.* Foundations and Trends in Machine
  Learning 3(1), 2011. – skalierte Form, Residuen und Abbruchkriterien, Über-Relaxation,
  Residual Balancing.
- Y. Wang, W. Yin, J. Zeng: *Global Convergence of ADMM in Nonconvex Nonsmooth Optimization.*
  Journal of Scientific Computing, 2019. – Konvergenzaussagen für nichtkonvexes ADMM.
- B. Wohlberg: *ADMM Penalty Parameter Selection by Residual Balancing.* arXiv:1704.06209, 2017.
  – normierte Residuen für adaptives ρ.

## Verteilter OPF
- B. H. Kim, R. Baldick: *Coarse-Grained Distributed Optimal Power Flow.* IEEE Transactions on
  Power Systems 12(2), 1997. – Zerlegung an Grenzbussen, Kopien von Randgrößen.
- T. Erseghe: *Distributed Optimal Power Flow Using ADMM.* IEEE Transactions on Power Systems
  29(5), 2014. – ADMM für AC-OPF, Konsens über Spannungen.
- S. Mhanna, G. Verbič, A. C. Chapman: *Adaptive ADMM for Distributed AC Optimal Power Flow.*
  IEEE Transactions on Power Systems 34(3), 2019. – adaptive und kantenabhängige Strafparameter.
- A. Engelmann, Y. Jiang, T. Mühlpfordt, B. Houska, T. Faulwasser: *Toward Distributed OPF Using
  ALADIN.* IEEE Transactions on Power Systems 34(1), 2019. – Alternative mit Konvergenzgarantie
  unter Annahmen, Vergleich mit ADMM.
- D. K. Molzahn et al.: *A Survey of Distributed Optimization and Control Algorithms for Electric
  Power Systems.* IEEE Transactions on Smart Grid 8(6), 2017.

## AC-OPF, Nichtkonvexität und Relaxierungen
- S. H. Low: *Convex Relaxation of Optimal Power Flow – Part I/II.* IEEE Transactions on Control of
  Network Systems 1(1)/(2), 2014.
- R. A. Jabr: *Radial Distribution Load Flow Using Conic Programming.* IEEE Transactions on Power
  Systems 21(3), 2006. – SOCP-Relaxierung (Grundlage von `tools/socp_lower_bound.py`).
- J. Lavaei, S. H. Low: *Zero Duality Gap in Optimal Power Flow Problem.* IEEE Transactions on
  Power Systems 27(1), 2012.
- F. Capitanescu: *Critical Review of Recent Advances and Further Developments Needed in AC Optimal
  Power Flow.* Electric Power Systems Research 136, 2016.

## Daten und Werkzeuge
- R. D. Zimmerman, C. E. Murillo-Sánchez, R. J. Thomas: *MATPOWER: Steady-State Operations,
  Planning, and Analysis Tools for Power Systems Research and Education.* IEEE Transactions on
  Power Systems 26(1), 2011. – `case118`, MATPOWER 8.1.
- S. Babaeinejadsarookolaee et al.: *The Power Grid Library for Benchmarking AC Optimal Power Flow
  Algorithms.* arXiv:1908.02788, 2019. – PGLib-OPF, Variante `pglib_opf_case118_ieee` (v23.07).
- L. Thurner et al.: *pandapower – An Open-Source Python Tool for Convenient Modeling, Analysis,
  and Optimization of Electric Power Systems.* IEEE Transactions on Power Systems 33(6), 2018.
  – PYPOWER/PIPS-Referenz.
- Gurobi Optimization: *Gurobi Optimizer Reference Manual*, Version 13.0 – nichtlineare
  Nebenbedingungen, `OptimalityTarget`, `NLBarIterLimit`.

## Vorarbeit
- M. B. Iwersen: *Dezentrale Berechnung von Optimal Power Flow auf verteilten Rechenressourcen
  anhand des IEEE 118-Bus-Systems.* Bachelorarbeit, Helmut-Schmidt-Universität / Universität der
  Bundeswehr Hamburg, 2025.
