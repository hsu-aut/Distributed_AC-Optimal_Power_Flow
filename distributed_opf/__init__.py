"""Distributed nonconvex AC-OPF with ADMM (IEEE 118-bus study).

- config / matpower_case: topology data and MATPOWER import
- pwl_ac: AC branch-flow model (historical name; full mode uses native nonlinear equations)
- admm_ac / admm_params / consensus_penalty / edge_penalty: local problems and ADMM steps
- agent_ac_mpi / neighbor_exchange / mpi_metrics: MPI agents and neighbor communication
- partitioning: bus-to-agent assignment
- ac_validation / ac_feasibility: independent AC feasibility checks
- live_viz: optional live snapshots for viz/
"""

from distributed_opf.config import load_topology_config

__all__ = ["load_topology_config"]
