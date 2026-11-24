# Distributed AC Optimal Power Flow on a Raspberry Pi Cluster (IEEE 118-bus)

Code, configuration and evaluation data for a consensus-ADMM solution of the full nonconvex
AC optimal power flow problem, executed on a cluster of 119 Raspberry Pi 5 worker nodes
coupled through MPI.

The decomposition ranges from 3 partitions down to one agent per bus. Every agent solves its
own AC-OPF subproblem with all power flow equations and operating limits as hard constraints;
neighbouring agents exchange only local copies of voltage magnitude and angle at the cut
edges. No linearisation, no piecewise-linear approximation and no centralised post-processing
are used. The assembled operating point is checked by an independent AC validation that is
separate from the optimisation model.

## Main findings

| | |
|---|---|
| Accepted runs | 67 distributed and 10 central runs of the main campaign, 16 runs of the PGLib cross-check |
| Cost deviation from the central local optimum | below 0.024 % up to 64 agents, 0.059 % for 118 bus agents |
| Admittance-weighted penalty | 17 % to 44 % fewer iterations than a uniform penalty |
| End-to-end runtime | no speedup: the fastest distributed configuration needs 17x the central job time |
| Crossover compute vs. communication | at about 3.7 buses per subproblem |

## Layout

| Path | Contents |
|---|---|
| `distributed_opf/` | solver: local AC-OPF subproblem, consensus ADMM, MPI agent, AC validation |
| `tools/` | campaign driver, aggregation, figures and tables, auxiliary studies |
| `tests/` | unit and integration tests, including a Ybus comparison against PYPOWER |
| `data/` | unmodified MATPOWER `case118.m` and PGLib-OPF `case118_ieee.m` with their licences |
| `topology_*.yaml` | imported topologies with source checksums |
| `docs/` | formulation, measurement protocol, campaign evaluations, limitations (German) |
| `validation/` | compact evidence per campaign: manifests, results, AC checks, rank metrics |
| `ergebnisse/` | figures and tables of the evaluation (PNG, PDF, CSV, Markdown) |

## Reproducing

```bash
conda env create -f environment.yml
conda activate distributed_opf
pytest -q
```

A single central reference run:

```bash
python central_ac_opf.py --topology topology_ieee118_full.yaml --threads 1
```

A distributed run with 16 partitions, matching the frozen campaign configuration:

```bash
mpirun -n 16 python run_mpi_cluster.py --topology topology_ieee118_full.yaml \
  --partitioner connected_impedance --edge-rho admittance --edge-rho-exponent 1 \
  --rho 3e5 --agents 16 --abs-tol 1e-4 --rel-tol 1e-4 --conv-check-every 10 \
  --centered-penalty --threads 1 --timing-trace
```

Gurobi is required for the local subproblems and is not included. `docs/CLUSTER_RUNBOOK.md`
describes the cluster setup, `docs/MEASUREMENT_PROTOCOL.md` the frozen measurement protocol.

Figures and tables are regenerated from raw campaign data with `tools/thesis_package.py`;
`PROVENANCE.md` records which software state produced which measurement series.

## Scope

The results hold for the investigated configurations of the IEEE 118-bus system. They are
neither a general convergence guarantee for nonconvex AC-OPF nor a statement about fault
tolerance: the implementation does not survive the loss of an agent. `docs/LIMITATIONS.md`
lists the known limitations in full.
