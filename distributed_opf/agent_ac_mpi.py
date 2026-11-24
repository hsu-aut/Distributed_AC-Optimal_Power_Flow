"""MPI-Agent for distributed nonconvex AC-OPF (OPF_MODE=ac)."""

from __future__ import annotations

import copy
import json
import math
import os
import pathlib
import sys
import time
import hashlib
from dataclasses import asdict
from typing import Dict, List, Optional, Set, Tuple, Any

import numpy as np
from mpi4py import MPI
from distributed_opf.mpi_metrics import measured_world

from distributed_opf.admm_params import AdmmParams
from distributed_opf.admm_ac import (
    AcLocalState,
    AcOpfLocalProblem,
    AcOpfSubnetProblem,
    admm_u_step_ac,
    admm_x_step_ac,
    admm_z_step_ac,
    decode_subnet_result,
    encode_subnet_result,
    SOLVER_METRICS,
)
from distributed_opf.config import load_topology_config
from distributed_opf.ac_feasibility import balance_from_agent_states
from distributed_opf.ac_validation import validate_ac, AcTolerances
from distributed_opf.live_viz import build_local_buses_ac, publish_live_snapshot
from distributed_opf.neighbor_exchange import NeighborExchange
from distributed_opf.edge_penalty import EdgePenalty
from distributed_opf.consensus_residuals import consensus_ratios, consensus_terms
from distributed_opf.initialization import read_initial_state
from distributed_opf.pwl_ac import AcPwlParams


def _wtime() -> float:
    try:
        return MPI.Wtime()
    except Exception:
        return time.time()


# Per-iteration wall-clock phases recorded on every rank (seconds).
TIMING_PHASES = ("vt_exchange", "x_solve", "solve_status", "consensus_exchange", "zu_update",
                 "convergence", "monitor", "io", "convergence_mpi", "console")


def timing_statistics(samples: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
    """Totals and distribution of per-iteration phase times."""
    out = {}
    for name, values in samples.items():
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            out[name] = {"total_s": 0.0, "count": 0}
            continue
        out[name] = {"total_s": float(arr.sum()), "count": int(arr.size), "mean_s": float(arr.mean()),
                     "p50_s": float(np.percentile(arr, 50)), "p95_s": float(np.percentile(arr, 95)),
                     "max_s": float(arr.max())}
    return out


RESIDUAL_TRACE_COLUMNS = ("iteration", "max_rank_primal_residual", "max_rank_dual_residual",
                          "max_rank_primal_ratio", "max_rank_dual_ratio", "global_primal_residual",
                          "global_dual_residual", "global_primal_ratio", "global_dual_ratio",
                          # Boyd matrix form with A^T aggregation (distributed_opf/consensus_residuals.py)
                          "boyd_a_max_rank_primal_ratio", "boyd_a_max_rank_dual_ratio",
                          "boyd_a_global_primal_ratio", "boyd_a_global_dual_ratio")


def global_boyd_ratios(sums, abs_tol: float, rel_tol: float) -> Dict[str, float]:
    """Global stopping quantities (Boyd et al. 2011, eq. 3.12) from rank sums.

    sums = (m, ||r||², ||s||², ||x||², ||z||², ||rho*u||²) summed over all ranks.
    """
    m, r2, s2, x2, z2, l2 = (float(v) for v in sums)
    r, s = math.sqrt(r2), math.sqrt(s2)
    eps_pri = math.sqrt(m) * abs_tol + rel_tol * max(math.sqrt(x2), math.sqrt(z2))
    eps_dual = math.sqrt(m) * abs_tol + rel_tol * math.sqrt(l2)
    return {"r": r, "s": s, "eps_pri": eps_pri, "eps_dual": eps_dual,
            "primal_ratio": r / eps_pri if eps_pri > 0 else math.inf,
            "dual_ratio": s / eps_dual if eps_dual > 0 else math.inf}


def balanced_rho(rho: float, primal_ratio: float, dual_ratio: float, mu: float, tau: float,
                 rho_min: float, rho_max: float) -> float:
    """Residual balancing on dimensionless residual quotients, clamped to [rho_min, rho_max]."""
    if primal_ratio > mu * dual_ratio:
        return min(rho_max, rho * tau)
    if dual_ratio > mu * primal_ratio:
        return max(rho_min, rho / tau)
    return rho


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else int(float(value))


class AcAgent:
    """ADMM-Agent für AC-OPF (Single-Bus oder Multi-Bus)."""

    def __init__(
        self,
        bus_id: int = None,
        bus_ids: List[int] = None,
        neighbor_ids: List[int] = None,
        neighbor_ranks: List[int] = None,
        topology_path: str = None,
        bus_to_rank_map: Dict[int, int] = None,
    ):
        self.world = measured_world()
        self.rank = self.world.Get_rank()
        self.size = self.world.Get_size()

        if bus_ids is not None:
            self.bus_ids = sorted(bus_ids)
            self._single_bus_mode = False
            self.bus_id = self.bus_ids[0]
        elif bus_id is not None:
            self.bus_id = bus_id
            self.bus_ids = [bus_id]
            self._single_bus_mode = True
        else:
            raise ValueError("AcAgent benötigt bus_id oder bus_ids")

        self.topology_path = topology_path
        if not self._single_bus_mode:
            if neighbor_ranks is None or bus_to_rank_map is None:
                raise ValueError("Multi-Bus benötigt neighbor_ranks und bus_to_rank_map")
            self.neighbor_ranks = neighbor_ranks
            self.bus_to_rank_map = bus_to_rank_map
            self.neighbor_ids = []
        else:
            if neighbor_ids is None:
                raise ValueError("Single-Bus benötigt neighbor_ids")
            self.neighbor_ids = neighbor_ids
            self.neighbor_ranks = []
            self.bus_to_rank_map = {}

        self.params = AdmmParams.from_env()
        self.strict = os.getenv("ADMM_STRICT", "1") == "1"
        self._run_start = _wtime()
        self._cpu_start = time.process_time()
        self._validation = None
        self._validation_ok = False
        self.pwl = AcPwlParams.from_env()
        self._adaptive_rho_enabled = os.getenv("ADMM_ADAPTIVE_RHO", "0").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        self._adaptive_rho_metric = os.getenv("ADMM_ADAPTIVE_RHO_METRIC", "max").strip().lower()
        self._adaptive_rho_mu = float(os.getenv("ADMM_ADAPTIVE_RHO_MU", "10"))
        self._adaptive_rho_tau = float(os.getenv("ADMM_ADAPTIVE_RHO_TAU", "2"))
        self._adaptive_rho_min = float(os.getenv("ADMM_RHO_MIN", "100"))
        self._adaptive_rho_max = float(os.getenv("ADMM_RHO_MAX", "1e5"))
        self._adaptive_rho_period = int(float(os.getenv("ADMM_ADAPTIVE_RHO_PERIOD", "10")))
        # 0 = adapt for the whole run; otherwise no penalty change after this iteration.
        self._adaptive_rho_until = int(float(os.getenv("ADMM_ADAPTIVE_RHO_UNTIL", "0")))
        self._rho_history: List[Dict[str, float]] = []
        self.rho_updates_total = 0
        self.iters_since_rho_update = 0
        # Legacy heuristics from the simplified cases (stall restart, best iterate, two-phase
        # slack): all off by default and not used in any reported full-model run.
        self._stall_iters = int(float(os.getenv("ADMM_STALL_ITERS", "0")))
        # stall: "up" erhöht rho (Konsensdruck), "down" senkt, "hold" nur Best-Restore
        self._stall_rho_action = os.getenv("ADMM_STALL_RHO_ACTION", "up").strip().lower()
        self._iters_since_best = 0
        self._close_push_done = False
        self._close_push_good = 0
        self._close_push_factor = float(os.getenv("ADMM_CLOSE_PUSH_FACTOR", "1.5"))
        self._close_push_band = float(os.getenv("ADMM_CLOSE_PUSH_BAND", "2.5e-3"))
        self._close_push_need = int(float(os.getenv("ADMM_CLOSE_PUSH_ITERS", "20")))
        # Zwei-Phasen-Slack: Phase A weich/penalty, ab harden_iter Phase B härter
        self._slack_harden_iter = int(float(os.getenv("ADMM_SLACK_HARDEN_ITER", "0") or "0"))
        self._phase_b_slack_weight = float(os.getenv("ADMM_PHASE_B_P_SLACK_WEIGHT", "1e12"))
        self._phase_b_use_slack = os.getenv("ADMM_PHASE_B_USE_SLACK", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        self._slack_phase = "A"
        self._slack_phase_applied = False
        # Phase-A-Gewichte merken (Env kann Phase B überschreiben)
        self._phase_a_p_slack_weight = float(self.params.p_slack_weight)
        self._phase_a_q_slack_weight = float(self.pwl.q_slack_weight)
        self._phase_a_use_slack = bool(self.params.use_slack)
        self._phase_a_balance_all = bool(self.pwl.balance_slack_all_buses)

        if topology_path:
            self.topology = load_topology_config(topology_path)
            self.pwl.configure_topology(self.topology)
            # Separate ADMM variant; None means standard ADMM with uniform rho.
            self.edge_penalty = EdgePenalty.from_env(self.topology)
            if self.topology.full_ac_model and (not self.strict or self.params.use_slack):
                raise ValueError("Full AC ADMM requires strict hard-balance settings; use tools/run_ac_local.py")
            if self.topology.full_ac_model and (not 0.0 < self.params.relaxation < 2.0 or os.getenv("ADMM_JACOBI", "1").lower() not in ("0", "false", "off")):
                raise ValueError("Full AC ADMM requires unrelaxed updates and optimized boundary copies; use tools/run_ac_local.py")
        else:
            self.topology = None
            raise RuntimeError("AC-Agent benötigt TOPOLOGY_CONFIG")

        if self._single_bus_mode:
            # Stagger/batch Gurobi model builds — 118 concurrent Academic WLS
            # checkouts often abort as EXIT:1 with 0 agent logs (no traceback).
            try:
                build_batch = max(1, int(float(os.getenv("ADMM_BUILD_BATCH", "8") or "8")))
            except Exception:
                build_batch = 8
            self.problem = None
            for start in range(0, self.size, build_batch):
                if start <= self.rank < start + build_batch:
                    print(
                        f"[Rank {self.rank}] building local MIP "
                        f"(bus {self.bus_id}, batch {start}-{start + build_batch - 1})...",
                        flush=True,
                    )
                    try:
                        self.problem = self._create_local_problem(self.bus_id)
                    except Exception as exc:
                        print(
                            f"[Rank {self.rank}] local build FAILED: {exc}",
                            flush=True,
                        )
                        raise
                    print(f"[Rank {self.rank}] local MIP ready", flush=True)
                self.world.Barrier()
            if self.problem is None:
                raise RuntimeError(f"Rank {self.rank}: local problem not built")
            self.state = AcLocalState(v=1.0, va=0.0)
            self.problems = {self.bus_id: self.problem}
            self.states = {self.bus_id: self.state}
            self.subnet_problem = None
            self.cut_edge_lines = []
            self.z_vars = {}
            self.u_vars = {}
            self.zq_vars = {}
            self.uq_vars = {}
        else:
            # Multi-Bus: nur Subnetz-MIP — keine 1-Bus-Gurobi-Modelle pro Bus
            # (spart WLS-Tokens, Init-Zeit und vermeidet License-Starvation).
            self.problems = {}
            self.problem = None
            self.states = {bid: AcLocalState(v=1.0, va=0.0) for bid in self.bus_ids}
            self.state = self.states[self.bus_id]
            self.cut_edge_buses = self._get_cut_edge_buses()
            self.cut_edges = self._build_cut_edges()
            self.cut_edge_lines = self._get_cut_edge_lines()
            # Serialize Gurobi model builds across ranks. Concurrent NonConvex
            # subnet builds (esp. 4×~30-bus) can abort Academic WLS with no
            # Python traceback (seen as EXIT:1 right after LicenseID).
            world_size = int(self.world.Get_size())
            self.subnet_problem = None
            for build_rank in range(world_size):
                if self.rank == build_rank:
                    print(
                        f"[Rank {self.rank}] building subnet AC model "
                        f"({len(self.bus_ids)} buses)...",
                        flush=True,
                    )
                    try:
                        self.subnet_problem = AcOpfSubnetProblem(
                            agent_id=self.rank,
                            bus_ids=self.bus_ids,
                            topology=self.topology,
                            params=self.params,
                            cut_edges=self.cut_edges,
                            pwl=self.pwl,
                        )
                    except Exception as exc:
                        print(
                            f"[Rank {self.rank}] subnet build FAILED: {exc}",
                            flush=True,
                        )
                        raise
                    print(f"[Rank {self.rank}] subnet AC model ready", flush=True)
                self.world.Barrier()
            if self.subnet_problem is None:
                raise RuntimeError(f"Rank {self.rank}: subnet_problem not built")
            # V/θ-Konsens auf Cut-Edges (nicht P/Q-Antisymmetrie)
            self.z_v = {(i, j): 1.0 for (i, j) in self.cut_edge_lines}
            self.u_v = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            self.z_th = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            self.u_th = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            self.z_v_own = {(i, j): 1.0 for (i, j) in self.cut_edge_lines}
            self.u_v_own = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            self.z_th_own = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            self.u_th_own = {(i, j): 0.0 for (i, j) in self.cut_edge_lines}
            # Legacy aliases (unused)
            self.z_vars = self.z_v
            self.u_vars = self.u_v
            self.zq_vars = self.z_th
            self.uq_vars = self.u_th

        if self._single_bus_mode:
            consensus_edges = [(self.bus_id, j, j) for j in self.problem.neighbors]
        else:
            consensus_edges = [(i, j, self.bus_to_rank_map[j]) for (i, j) in self.cut_edge_lines]
        # Collective setup: every rank builds its neighbor graph at the same point.
        self.neighbor_exchange = NeighborExchange(self.world, consensus_edges)
        self._edge_rows = [(rank, local, remote) for rank in self.neighbor_exchange.neighbor_ranks
                           for local, remote in self.neighbor_exchange.edges[rank]]
        # Neighbors' own V/θ from the last consensus exchange; states do not change
        # between that exchange and the next x-step unless they are restored.
        self._neighbor_vt_cache: Optional[Dict[Tuple[int, int], Tuple[float, float]]] = None
        self._log_every = _env_int("ADMM_LOG_EVERY", 1)
        self._timing: Dict[str, List[float]] = {name: [] for name in TIMING_PHASES}
        self._residual_trace: List[Tuple[float, ...]] = []
        self._last_boyd: Optional[Dict[str, float]] = None
        # Reporting only: Boyd matrix-form consensus terms of the last z/u-update and split timings.
        self._last_boyd_a: Optional[Dict[str, float]] = None
        self._consensus_terms = (0.0,) * 7
        self._t_conv_mpi = 0.0
        self._t_console = 0.0
        self._wall_iterations = float("nan")

        self._init_duals()
        self.iteration = 0
        self.converged = False
        self.min_iters = int(os.getenv("ADMM_MIN_ITERS", "5"))
        self._track_best = os.getenv("ADMM_BEST_ITERATE", "0").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        self._best_max_prim = float("inf")
        self._best_iter = -1
        self._best_sum_ps = 0.0
        self._best_sum_qs = 0.0
        self._best_snapshots: Optional[Dict[int, AcLocalState]] = None
        self._convergence_kind: Optional[str] = None
        self._phys_gate = os.getenv("ADMM_PHYS_GATE", "0").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        try:
            self._phys_check_every = int(float(os.getenv("ADMM_PHYS_CHECK_EVERY", "10") or "10"))
        except Exception:
            self._phys_check_every = 10
        # 0 = disabled (important for buswise-118; do NOT clamp to >=1)
        self._last_phys_max_dp = float("inf")
        self._last_phys_max_dq = float("inf")
        self._last_global_max_prim = float("inf")
        self._last_global_max_dual = float("inf")
        self._last_global_sum_ps = 0.0
        self._last_global_sum_qs = 0.0
        print(f"[AC] Rank {self.rank}: buses={self.bus_ids}, single={self._single_bus_mode}")

    def _incident_lines(self, bus_id: int) -> List[Tuple[int, int, float, float, float, float]]:
        """Alle an bus_id anliegenden Leitungen (inkl. Parallelzweige), Richtung i→j."""
        out: List[Tuple[int, int, float, float, float, float]] = []
        for ell, line in enumerate(self.topology.lines):
            if line.from_bus == bus_id:
                j = line.to_bus
            elif line.to_bus == bus_id:
                j = line.from_bus
            else:
                continue
            out.append(
                (ell, j, line.r_pu, line.x_pu, line.b_shunt_pu, line.thermal_limit_pu)
            )
        if not out:
            bus_cfg = self.topology.buses[bus_id]
            if bus_cfg.neighbors:
                raise ValueError(f"Bus {bus_id}: Nachbarn in YAML, aber keine Leitung in lines[]")
        return sorted(out, key=lambda t: t[0])

    def _create_local_problem(self, bus_id: int) -> AcOpfLocalProblem:
        bus = self.topology.buses[bus_id]
        return AcOpfLocalProblem(
            bus_id=bus_id,
            is_generator=bus.is_generator,
            is_slack=bus.is_slack,
            pg_bounds=(bus.pg_min, bus.pg_max),
            qg_bounds=(bus.qg_min, bus.qg_max),
            v_bounds=(bus.v_min_pu, bus.v_max_pu),
            cost_quad=bus.cost_quad,
            cost_lin=bus.cost_lin,
            demand_p=bus.demand_p,
            demand_q=bus.demand_q,
            incident_lines=self._incident_lines(bus_id),
            base_mva=self.topology.base_mva,
            topology=self.topology,
            pwl=self.pwl,
            neighbor_v_bounds={j: (self.topology.buses[j].v_min_pu,
                                   self.topology.buses[j].v_max_pu)
                               for j in bus.neighbors},
        )

    def _init_duals(self) -> None:
        for bus_id in self.bus_ids:
            state = self.states[bus_id]
            if self._single_bus_mode:
                neighbors = list(self.problems[bus_id].neighbors)
            else:
                neighbors = list(self.topology.buses[bus_id].neighbors)
            if not state.uad:
                state.uad = {j: 0.0 for j in neighbors}
            if not state.zad:
                state.zad = {j: 0.0 for j in neighbors}
            if not state.uq:
                state.uq = {j: 0.0 for j in neighbors}
            if not state.zq:
                state.zq = {j: 0.0 for j in neighbors}
            if not state.uv:
                state.uv = {j: 0.0 for j in neighbors}
            if not state.zv:
                state.zv = {j: 1.0 for j in neighbors}
            if not state.uth:
                state.uth = {j: 0.0 for j in neighbors}
            if not state.zth:
                state.zth = {j: 0.0 for j in neighbors}
            if not state.uv_own:
                state.uv_own = {j: 0.0 for j in neighbors}
            if not state.zv_own:
                state.zv_own = {j: 1.0 for j in neighbors}
            if not state.uth_own:
                state.uth_own = {j: 0.0 for j in neighbors}
            if not state.zth_own:
                state.zth_own = {j: 0.0 for j in neighbors}

    def _get_cut_edge_buses(self) -> Set[int]:
        cut: Set[int] = set()
        for bus_id in self.bus_ids:
            for j in self.topology.buses[bus_id].neighbors:
                if j not in self.bus_ids:
                    cut.add(bus_id)
        return cut

    def _build_cut_edges(self) -> Dict[int, List[Tuple[int, int]]]:
        cut_edges: Dict[int, List[Tuple[int, int]]] = {}
        for bus_id in self.bus_ids:
            for j in self.topology.buses[bus_id].neighbors:
                if j in self.bus_ids:
                    continue
                nr = self.bus_to_rank_map.get(j)
                if nr is None or nr == self.rank:
                    continue
                cut_edges.setdefault(nr, []).append((bus_id, j))
        return cut_edges

    def _get_cut_edge_lines(self) -> List[Tuple[int, int]]:
        lines: List[Tuple[int, int]] = []
        seen: Set[Tuple[int, int]] = set()
        for edges in self.cut_edges.values():
            for e in edges:
                if e not in seen:
                    seen.add(e)
                    lines.append(e)
        return lines

    def run(self) -> None:
        if os.getenv("ADMM_RESUME_DIR"):
            self._restore_checkpoint(pathlib.Path(os.environ["ADMM_RESUME_DIR"]))
        elif os.getenv("ADMM_INIT_STATE"):
            self._apply_initial_state(pathlib.Path(os.environ["ADMM_INIT_STATE"]))
        solve_start = _wtime()
        max_iters = int(self.params.max_iters)
        for _ in range(max(0, max_iters-self.iteration)):
            self._maybe_harden_slack_phase()
            if self._single_bus_mode:
                self._epoch_single()
            else:
                self._epoch_multi()
            t_io = time.perf_counter()
            self._log_iteration()
            t_monitor = time.perf_counter()
            self._timing["io"].append(t_monitor - t_io)
            self._maybe_check_physics()
            t_convergence = time.perf_counter()
            ok = self._check_global_convergence()
            self._maybe_track_best_iterate()
            best_ok = self._maybe_accept_best_convergence()
            t_gate = time.perf_counter()
            conv_mpi, console = self._t_conv_mpi, self._t_console
            self._t_conv_mpi = self._t_console = 0.0
            self._timing["convergence_mpi"].append(conv_mpi)
            self._timing["console"].append(console)
            self._timing["convergence"].append(t_gate - t_convergence - conv_mpi - console)
            if (self._phys_gate or self.strict) and (ok or best_ok):
                self._update_physics_metrics()
                phys_ok = self._phys_ok()
                if not phys_ok:
                    if self.rank == 0:
                        print(
                            f"[AC] Consensus ok but physics not met: "
                            f"max|dP|={self._last_phys_max_dp:.3e} "
                            f"max|dQ|={self._last_phys_max_dq:.3e} "
                            f"(phys_tol={self.params.phys_tol:.3e})",
                            flush=True,
                        )
                    ok = False
                    best_ok = False
            # Global AC validation is observation, not part of a distributed ADMM step.
            self._timing["monitor"].append((t_convergence - t_monitor) + (time.perf_counter() - t_gate))
            if self.iteration >= self.min_iters and (ok or best_ok):
                self.converged = True
                self._convergence_kind = "best_iterate" if best_ok and not ok else "current"
                self._log_convergence_state()
                if self.rank == 0:
                    if best_ok and not ok:
                        print(
                            f"[AC] Converged on best iterate {self._best_iter} "
                            f"(max_prim={self._best_max_prim:.2e}, "
                            f"sum_|ps|={self._best_sum_ps:.3e}, sum_|qs|={self._best_sum_qs:.3e}) "
                            f"at wall-iter {self.iteration}",
                            flush=True,
                        )
                    else:
                        print(f"[AC] Converged at iteration {self.iteration}", flush=True)
                self._publish_live_viz(converged=True)
                break
            self._adaptive_rho_update()
            self._maybe_stall_restart()
            self._maybe_close_push_rho()
            self._publish_live_viz(converged=False)
        # Iteration loop only: excludes final validation, checkpoint and summary.
        self._wall_iterations = self.world.allreduce(_wtime() - solve_start, op=MPI.MAX)
        if not self.converged and self._log_every != 1 and (self._log_every <= 0 or self.iteration % self._log_every):
            for bus_id in self.bus_ids:
                self._write_log(bus_id, self.states[bus_id])
        self._finalize_best_iterate()
        self._update_physics_metrics()
        self._write_checkpoint(force=True)
        self._wall_solve = self.world.allreduce(_wtime() - solve_start, op=MPI.MAX)
        self._wall_total = self.world.allreduce(_wtime() - self._run_start, op=MPI.MAX)
        self._write_run_summary()
        if self.rank == 0:
            print(f"[AC] Finished after {self.iteration} iterations, converged={self.converged}")
        self.neighbor_exchange.close()

    def _maybe_harden_slack_phase(self) -> None:
        """Ab ADMM_SLACK_HARDEN_ITER: Slack härter (höhere Penalty oder aus)."""
        if self._slack_harden_iter <= 0 or self._slack_phase_applied:
            return
        # Nach abgeschlossener Phase-A-Iteration umschalten (iteration zählt fertige Iters)
        if self.iteration < self._slack_harden_iter:
            return
        self._slack_phase_applied = True
        self._slack_phase = "B"
        if self._phase_b_use_slack:
            self.params.use_slack = True
            self.params.p_slack_weight = float(self._phase_b_slack_weight)
            self.pwl.use_q_slack = True
            self.pwl.q_slack_weight = float(self._phase_b_slack_weight)
            self.pwl.balance_slack_all_buses = True
        else:
            self.params.use_slack = False
            self.pwl.balance_slack_all_buses = False
            self.pwl.use_q_slack = False
            # Soft-Fallback nur noch ein-shot (sticky-rebuild); optional hart abschalten
            allow_fb = os.getenv("ADMM_PHASE_B_ALLOW_FALLBACK", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
            os.environ["ADMM_ALLOW_SLACK_FALLBACK"] = "1" if allow_fb else "0"
        # Modelle neu bauen, damit Bounds/Obj zur Phase B passen
        if self._single_bus_mode:
            self.problem.request_rebuild()
        else:
            if self.subnet_problem is not None:
                self.subnet_problem.request_rebuild()
            for prob in self.problems.values():
                prob.request_rebuild()
        if self.rank == 0:
            if self._phase_b_use_slack:
                print(
                    f"[AC] Slack Phase B ab Iter {self.iteration + 1}: "
                    f"weight={self.params.p_slack_weight:.3e} (was {self._phase_a_p_slack_weight:.3e})",
                    flush=True,
                )
            else:
                print(
                    f"[AC] Slack Phase B ab Iter {self.iteration + 1}: USE_SLACK=0 (hart), "
                    f"slack_fallback=off",
                    flush=True,
                )

    def _epoch_single(self) -> None:
        k = self.iteration
        t0 = _wtime()
        t_vt = time.perf_counter()

        # Warmstart-Hinweise aus letztem Exchange (nicht hart fixieren)
        neighbor_states: Dict[int, AcLocalState] = {
            j: AcLocalState(v=v_j, va=th_j) for (_, j), (v_j, th_j) in self._neighbor_vt().items()
        }
        t_solve = time.perf_counter()
        self._timing["vt_exchange"].append(t_solve - t_vt)

        # Optional batch concurrent Gurobi optimizes (Academic WLS).
        # ADMM_SOLVE_BATCH<=0 → alle Ranks gleichzeitig (Jacobi).
        # Mit Hold-on-WLS-Fail ist Batching wieder sicher (kein Abort → kein Barrier-Hang).
        try:
            solve_batch = int(float(os.getenv("ADMM_SOLVE_BATCH", "16") or "16"))
        except Exception:
            solve_batch = 16
        if solve_batch <= 0:
            solve_batch = self.size
        else:
            solve_batch = max(1, min(solve_batch, self.size))
        x_out = None
        for start in range(0, self.size, solve_batch):
            if start <= self.rank < start + solve_batch:
                x_out = admm_x_step_ac(
                    self.problem, self.state, neighbor_states, self.params
                )
            if solve_batch < self.size:
                self.world.Barrier()
        if x_out is None:
            raise RuntimeError(f"Rank {self.rank}: x-step not executed")
        t_status = time.perf_counter()
        self._timing["x_solve"].append(t_status - t_solve)
        if self.strict:
            solved = np.array([1 if getattr(self.problem, "last_solve_ok", False) else 0], dtype="i")
            self.world.Allreduce(MPI.IN_PLACE, solved, op=MPI.MIN)
            if not solved[0]:
                raise RuntimeError("At least one local AC solve failed; run cannot be accepted")
        (
            v,
            va,
            pg,
            qg,
            obj,
            pij,
            qij,
            v_copy,
            va_copy,
            p_slack,
            q_slack,
            gen_cost,
            line_s_max,
            line_s_mean,
            gen_s_max,
        ) = x_out

        self.state._v_prev = self.state.v
        self.state._va_prev = self.state.va
        self.state._pg_prev = self.state.pg
        self.state.v = v
        self.state.va = va
        self.state.pg = pg
        self.state.qg = qg
        self.state.pij = pij
        self.state.qij = qij
        self.state.v_copy = v_copy
        self.state.va_copy = va_copy
        self.state.objective_cost = obj
        self.state.p_slack = p_slack
        self.state.q_slack = q_slack
        self.state.generation_cost = gen_cost
        self.state.line_slack_max = line_s_max
        self.state.line_slack_mean = line_s_mean
        self.state.gen_slack_max = gen_s_max

        # Exchange with neighbors only: own V/θ, local copies and duals for V/θ consensus
        t_exchange = time.perf_counter()
        self._timing["solve_status"].append(t_exchange - t_status)
        st = self.state
        send = np.array([[st.v, st.va, st.v_copy[j], st.va_copy[j], st.uv_own[j], st.uth_own[j], st.uv[j], st.uth[j]]
                         for _, _, j in self._edge_rows], dtype="d").reshape(-1, 8)
        recv = self.neighbor_exchange.exchange(send)
        t_update = time.perf_counter()
        self._timing["consensus_exchange"].append(t_update - t_exchange)

        # Each neighbor row only carries the entries this bus's z-step reads.
        me = self.bus_id
        neigh_after: Dict[int, AcLocalState] = {}
        vt_cache: Dict[Tuple[int, int], Tuple[float, float]] = {}
        for (rank, _, j), row in zip(self._edge_rows, recv):
            neigh_after[j] = AcLocalState(
                v=float(row[0]), va=float(row[1]),
                v_copy={me: float(row[2])}, va_copy={me: float(row[3])},
                uv_own={me: float(row[4])}, uth_own={me: float(row[5])},
                uv={me: float(row[6])}, uth={me: float(row[7])},
            )
            vt_cache[(rank, j)] = (float(row[0]), float(row[1]))
        self._neighbor_vt_cache = vt_cache

        zv, zth, zv_own, zth_own, dual_res = admm_z_step_ac(
            self.problem, self.state, neigh_after, self.params
        )
        self.state.dual_res = dual_res
        before_u = self.state
        self.state = admm_u_step_ac(
            self.problem, self.state, zv, zth, zv_own, zth_own, self.params
        )
        self.state.dual_res = dual_res
        self.states[self.bus_id] = self.state
        self._consensus_terms = self._single_consensus_terms(before_u, self.state)

        self._last_timing = {"t_iter_total": _wtime() - t0}
        self._timing["zu_update"].append(time.perf_counter() - t_update)
        self.iteration = k + 1

    def _epoch_multi(self) -> None:
        k = self.iteration
        t0 = _wtime()
        if k == 0:
            print(f"[AC] Rank {self.rank}: first multi epoch starting", flush=True)
        t_vt = time.perf_counter()
        # Boundary values only; the subnet solve reads them as a fallback when z is missing.
        neighbor_v: Dict[int, Dict[int, float]] = {}
        neighbor_theta: Dict[int, Dict[int, float]] = {}
        for (rank, remote), (v_remote, th_remote) in self._neighbor_vt().items():
            neighbor_v.setdefault(rank, {})[remote] = v_remote
            neighbor_theta.setdefault(rank, {})[remote] = th_remote
        t_solve = time.perf_counter()
        self._timing["vt_exchange"].append(t_solve - t_vt)
        if k == 0:
            print(f"[AC] Rank {self.rank}: voltage exchange done → subnet solve", flush=True)

        result = self.subnet_problem.solve(
            neighbor_theta=neighbor_theta,
            neighbor_v=neighbor_v,
            z_v=self.z_v,
            u_v=self.u_v,
            z_th=self.z_th,
            u_th=self.u_th,
            z_v_own=self.z_v_own,
            u_v_own=self.u_v_own,
            z_th_own=self.z_th_own,
            u_th_own=self.u_th_own,
        )

        for bus_id in self.bus_ids:
            st = self.states[bus_id]
            st._v_prev = st.v
            st._va_prev = st.va
            st._pg_prev = st.pg
            st.v = result.v.get(bus_id, 1.0)
            st.va = result.va.get(bus_id, 0.0)
            st.pg = result.pg.get(bus_id, 0.0)
            st.qg = result.qg.get(bus_id, 0.0)
            st.p_slack = result.p_slack.get(bus_id, 0.0)
            st.q_slack = result.q_slack.get(bus_id, 0.0)
            st.objective_cost = result.objective_cost / max(1, len(self.bus_ids))
            bus = self.topology.buses[bus_id]
            st.generation_cost = bus.cost_quad*st.pg*st.pg + bus.cost_lin*st.pg + bus.cost_const if bus.is_generator else 0.0
            st.line_slack_max = result.line_slack_max
            st.line_slack_mean = result.line_slack_mean
            st.pij = {}
            st.qij = {}
            for (ell, i, j), pval in result.pij.items():
                if i == bus_id:
                    st.pij[ell] = pval
                    st.qij[ell] = result.qij[(ell, i, j)]
            st.v_copy = dict(result.v_copy or {})
            st.va_copy = dict(result.va_copy or {})

        # Exchange with neighbor ranks only: V/θ + copies + duals per cut edge
        t_exchange = time.perf_counter()
        self._timing["x_solve"].append(t_exchange - t_solve)
        # Subnet solve failures raise and abort all ranks; there is no status reduction.
        self._timing["solve_status"].append(0.0)
        send = np.array([[self.states[i].v, self.states[i].va,
                          self.states[i].v_copy[j], self.states[i].va_copy[j],
                          self.u_v_own.get((i, j), 0.0), self.u_th_own.get((i, j), 0.0),
                          self.u_v.get((i, j), 0.0), self.u_th.get((i, j), 0.0)]
                         for _, i, j in self._edge_rows], dtype="d").reshape(-1, 8)
        recv = self.neighbor_exchange.exchange(send)
        t_update = time.perf_counter()
        self._timing["consensus_exchange"].append(t_update - t_exchange)
        # Row for local edge (i, j): the owner of j sends its V_j, θ_j, its copy of bus i,
        # and its duals of edge (j, i).
        remote_rows: Dict[Tuple[int, int], np.ndarray] = {}
        vt_cache: Dict[Tuple[int, int], Tuple[float, float]] = {}
        for (rank, i, j), row in zip(self._edge_rows, recv):
            remote_rows[(i, j)] = row
            vt_cache[(rank, j)] = (float(row[0]), float(row[1]))
        self._neighbor_vt_cache = vt_cache

        alpha = float(getattr(self.params, "relaxation", 1.0))
        z_v_prev = dict(self.z_v)
        z_th_prev = dict(self.z_th)
        z_v_own_prev = dict(self.z_v_own)
        z_th_own_prev = dict(self.z_th_own)
        new_zv: Dict[Tuple[int, int], float] = {}
        new_zth: Dict[Tuple[int, int], float] = {}
        new_zv_own: Dict[Tuple[int, int], float] = {}
        new_zth_own: Dict[Tuple[int, int], float] = {}
        s2 = 0.0
        r2 = 0.0
        rho_v = float(self.params.effective_rho_v())
        rho_th = float(self.params.effective_rho_th())
        members = []

        for (i, j) in self.cut_edge_lines:
            v_i = float(result.v.get(i, 1.0))
            th_i = float(result.va.get(i, 0.0))
            v_j_copy = float((result.v_copy or {}).get(j, 1.0))
            th_j_copy = float((result.va_copy or {}).get(j, 0.0))
            row = remote_rows[(i, j)]
            v_j = float(row[0])
            th_j = float(row[1])
            v_i_at_j = float(row[2])
            th_i_at_j = float(row[3])

            u_v_j = float(self.u_v.get((i, j), 0.0))
            u_th_j = float(self.u_th.get((i, j), 0.0))
            u_v_own_j = float(self.u_v_own.get((i, j), 0.0))
            u_th_own_j = float(self.u_th_own.get((i, j), 0.0))
            u_v_j_at_j = float(row[4])
            u_th_j_at_j = float(row[5])
            u_v_i_at_j = float(row[6])
            u_th_i_at_j = float(row[7])

            zv_old = float(z_v_prev.get((i, j), v_j))
            zth_old = float(z_th_prev.get((i, j), th_j))
            zv_own_old = float(z_v_own_prev.get((i, j), v_i))
            zth_own_old = float(z_th_own_prev.get((i, j), th_i))

            if alpha != 1.0:
                v_j_copy_h = alpha * v_j_copy + (1.0 - alpha) * zv_old
                th_j_copy_h = alpha * th_j_copy + (1.0 - alpha) * zth_old
                v_i_h = alpha * v_i + (1.0 - alpha) * zv_own_old
                th_i_h = alpha * th_i + (1.0 - alpha) * zth_own_old
                v_j_h = alpha * v_j + (1.0 - alpha) * zv_old
                th_j_h = alpha * th_j + (1.0 - alpha) * zth_old
                v_i_at_j_h = alpha * v_i_at_j + (1.0 - alpha) * zv_own_old
                th_i_at_j_h = alpha * th_i_at_j + (1.0 - alpha) * zth_own_old
            else:
                v_j_copy_h, th_j_copy_h = v_j_copy, th_j_copy
                v_i_h, th_i_h = v_i, th_i
                v_j_h, th_j_h = v_j, th_j
                v_i_at_j_h, th_i_at_j_h = v_i_at_j, th_i_at_j

            zv = 0.5 * ((v_j_copy_h + u_v_j) + (v_j_h + u_v_j_at_j))
            zth = 0.5 * ((th_j_copy_h + u_th_j) + (th_j_h + u_th_j_at_j))
            zv_own = 0.5 * ((v_i_h + u_v_own_j) + (v_i_at_j_h + u_v_i_at_j))
            zth_own = 0.5 * ((th_i_h + u_th_own_j) + (th_i_at_j_h + u_th_i_at_j))
            new_zv[(i, j)] = zv
            new_zth[(i, j)] = zth
            new_zv_own[(i, j)] = zv_own
            new_zth_own[(i, j)] = zth_own
            factor_sq = self._edge_factor(i, j) ** 2
            s2 += rho_v**2 * factor_sq * ((zv-zv_old)**2 + (zv_own-zv_own_old)**2)
            s2 += rho_th**2 * factor_sq * ((zth-zth_old)**2 + (zth_own-zth_own_old)**2)

            rv = v_j_copy - zv
            rth = th_j_copy - zth
            rv_own = v_i - zv_own
            rth_own = th_i - zth_own
            r2 += rv * rv + rth * rth + rv_own * rv_own + rth_own * rth_own
            # Relaxed ADMM: u += x_hat - z (x_hat = x for alpha = 1); r stays x - z.
            self.u_v[(i, j)] = u_v_j + (v_j_copy_h - zv)
            self.u_th[(i, j)] = u_th_j + (th_j_copy_h - zth)
            self.u_v_own[(i, j)] = u_v_own_j + (v_i_h - zv_own)
            self.u_th_own[(i, j)] = u_th_own_j + (th_i_h - zth_own)
            factor = self._edge_factor(i, j)
            members.append((("copy_v", j), v_j_copy, zv, zv_old, self.u_v[(i, j)], rho_v * factor))
            members.append((("copy_th", j), th_j_copy, zth, zth_old, self.u_th[(i, j)], rho_th * factor))
            members.append((("own_v", i), v_i, zv_own, zv_own_old, self.u_v_own[(i, j)], rho_v * factor))
            members.append((("own_th", i), th_i, zth_own, zth_own_old, self.u_th_own[(i, j)], rho_th * factor))

        self._consensus_terms = consensus_terms(members)
        self.z_v = new_zv
        self.z_th = new_zth
        self.z_v_own = new_zv_own
        self.z_th_own = new_zth_own
        self.z_vars = self.z_v
        self.u_vars = self.u_v
        self.zq_vars = self.z_th
        self.uq_vars = self.u_th
        prim = math.sqrt(r2)
        dual = math.sqrt(s2)
        for bus_id in self.bus_ids:
            self.states[bus_id].prim_res = prim
            self.states[bus_id].dual_res = dual
        self.state = self.states[self.bus_id]
        self._last_timing = {"t_iter_total": _wtime() - t0}
        self._timing["zu_update"].append(time.perf_counter() - t_update)
        self.iteration = k + 1

    def _neighbor_vt(self) -> Dict[Tuple[int, int], Tuple[float, float]]:
        """Own V/θ of every neighbor bus, keyed by (rank, bus); exchanged only when not cached."""
        if self._neighbor_vt_cache is None:
            send = np.array([[self.states[local].v, self.states[local].va] for _, local, _ in self._edge_rows],
                            dtype="d").reshape(-1, 2)
            recv = self.neighbor_exchange.exchange(send)
            self._neighbor_vt_cache = {(rank, remote): (float(row[0]), float(row[1]))
                                       for (rank, _, remote), row in zip(self._edge_rows, recv)}
        return self._neighbor_vt_cache

    def _check_convergence_local(self) -> bool:
        eps_abs = float(self.params.abs_tol)
        eps_rel = float(self.params.rel_tol)
        rho_v = float(self.params.effective_rho_v())
        rho_th = float(self.params.effective_rho_th())
        if self._single_bus_mode:
            state = self.state
            m_i = max(1, len(self.problem.neighbors) * 4)
            r_norm = float(state.prim_res)
            s_norm = float(state.dual_res)
            x_vals = (
                [state.v, state.va]
                + list((state.v_copy or {}).values())
                + list((state.va_copy or {}).values())
            )
            z_vals = (
                list((state.zv or {}).values())
                + list((state.zth or {}).values())
                + list((state.zv_own or {}).values())
                + list((state.zth_own or {}).values())
            )
            # Scaled duals for Boyd ε_dual ~ … + ε_rel ||ρ u||
            u_scaled = []
            for j, val in (state.uv or {}).items():
                u_scaled.append(rho_v * self._edge_factor(self.bus_id, j) * float(val))
            for j, val in (state.uv_own or {}).items():
                u_scaled.append(rho_v * self._edge_factor(self.bus_id, j) * float(val))
            for j, val in (state.uth or {}).items():
                u_scaled.append(rho_th * self._edge_factor(self.bus_id, j) * float(val))
            for j, val in (state.uth_own or {}).items():
                u_scaled.append(rho_th * self._edge_factor(self.bus_id, j) * float(val))
        else:
            state = self.state
            m_i = max(1, len(self.cut_edge_lines) * 4)
            r_norm = float(state.prim_res)
            s_norm = float(state.dual_res)
            x_vals = []
            z_vals = (
                list(self.z_v.values())
                + list(self.z_th.values())
                + list(self.z_v_own.values())
                + list(self.z_th_own.values())
            )
            u_scaled = []
            for (i, j), val in (self.u_v or {}).items():
                u_scaled.append(rho_v * self._edge_factor(i, j) * float(val))
            for (i, j), val in (self.u_v_own or {}).items():
                u_scaled.append(rho_v * self._edge_factor(i, j) * float(val))
            for (i, j), val in (self.u_th or {}).items():
                u_scaled.append(rho_th * self._edge_factor(i, j) * float(val))
            for (i, j), val in (self.u_th_own or {}).items():
                u_scaled.append(rho_th * self._edge_factor(i, j) * float(val))
            for bus_id in self.bus_ids:
                x_vals.append(self.states[bus_id].v)
                x_vals.append(self.states[bus_id].va)
            # Boundary copies belong to the partition, not to each bus: count them once.
            x_vals.extend((self.states[self.bus_id].v_copy or {}).values())
            x_vals.extend((self.states[self.bus_id].va_copy or {}).values())

        def _euclid(vals):
            return math.sqrt(sum(float(v) ** 2 for v in vals)) if vals else 0.0

        eps_pri = (m_i ** 0.5) * eps_abs + eps_rel * max(_euclid(x_vals), _euclid(z_vals))
        # Boyd (skaliert): s ~ ρ Δz, ε_dual ~ … + ε_rel ||ρ u||
        eps_dual = (m_i ** 0.5) * eps_abs + eps_rel * _euclid(u_scaled)
        dual_ok = s_norm <= eps_dual
        # Unskaliertes Δz-Kriterium (hartes Abs-Gate): bei großem ρ sonst Dual-Fail
        rho_ref = max(rho_v, rho_th, 1e-12)
        s_unscaled = float(s_norm) / rho_ref
        if not self.strict:
            dual_ok = dual_ok or (s_unscaled <= float(eps_abs))
        # Primär: zusätzlich hartes Abs-Gate (sonst stoppt 118 früh wegen großem ||x|| in ε_pri)
        prim_ok = (r_norm <= eps_pri) and (r_norm <= float(eps_abs))
        # Squared norms for the global (Boyd) criterion, summed over ranks for reporting only.
        self._last_local_norms = (float(m_i), r_norm ** 2, s_norm ** 2, _euclid(x_vals) ** 2,
                                  _euclid(z_vals) ** 2, _euclid(u_scaled) ** 2)
        # Dimensionless quotients of the binding strict gates (<= 1 means satisfied).
        self._last_local_ratios = (r_norm / max(min(eps_pri, float(eps_abs)), 1e-300),
                                   s_norm / max(eps_dual, 1e-300))
        slack_tol = float(os.getenv("ADMM_SLACK_TOL", "1e-3"))
        if self._single_bus_mode:
            slack_ok = (
                abs(float(state.p_slack)) <= slack_tol
                and abs(float(state.q_slack)) <= slack_tol
            )
        else:
            slack_ok = True
            for bus_id in self.bus_ids:
                if abs(float(self.states[bus_id].p_slack)) > slack_tol:
                    slack_ok = False
                if abs(float(self.states[bus_id].q_slack)) > slack_tol:
                    slack_ok = False
        return prim_ok and dual_ok and slack_ok

    def _single_consensus_terms(self, before: AcLocalState, after: AcLocalState):
        """Boyd matrix-form consensus terms of this bus agent (reporting only)."""
        rho_v = float(self.params.effective_rho_v())
        rho_th = float(self.params.effective_rho_th())
        me = self.bus_id
        members = []
        for j in self.problem.neighbors:
            factor = self._edge_factor(me, j)
            v_c = float((before.v_copy or {}).get(j, 1.0))
            th_c = float((before.va_copy or {}).get(j, 0.0))
            # Previous z defaults mirror admm_z_step_ac.
            members.append((("copy_v", j), v_c, float(after.zv[j]), float((before.zv or {}).get(j, v_c)),
                            float(after.uv[j]), rho_v * factor))
            members.append((("copy_th", j), th_c, float(after.zth[j]), float((before.zth or {}).get(j, th_c)),
                            float(after.uth[j]), rho_th * factor))
            members.append((("own_v", me), float(before.v), float(after.zv_own[j]),
                            float((before.zv_own or {}).get(j, before._v_prev)), float(after.uv_own[j]), rho_v * factor))
            members.append((("own_th", me), float(before.va), float(after.zth_own[j]),
                            float((before.zth_own or {}).get(j, before._va_prev)), float(after.uth_own[j]), rho_th * factor))
        return consensus_terms(members)

    def _check_global_convergence(self) -> bool:
        local_ok = 1 if self._check_convergence_local() else 0
        abs_tol = float(self.params.abs_tol)
        rel_tol = float(self.params.rel_tol)

        # Globale Kennzahlen: max Residuen + Summe |Slack| (nicht nur Rank 0)
        prim = float(self.state.prim_res)
        dual = float(self.state.dual_res)
        if self._single_bus_mode:
            ps = abs(float(self.state.p_slack))
            qs = abs(float(self.state.q_slack))
        else:
            ps = sum(abs(float(self.states[b].p_slack)) for b in self.bus_ids)
            qs = sum(abs(float(self.states[b].q_slack)) for b in self.bus_ids)
        # One MAX reduction carries "any rank not converged" together with the residuals.
        ratio_primal, ratio_dual = self._last_local_ratios
        local_a = consensus_ratios(self._consensus_terms, abs_tol, rel_tol)
        max_pd = np.array([1.0 - local_ok, prim, dual, ratio_primal, ratio_dual,
                           local_a["primal_ratio"], local_a["dual_ratio"]], dtype=float)
        sum_sq = np.array([ps, qs, *self._last_local_norms, *self._consensus_terms], dtype=float)
        t_mpi = time.perf_counter()
        self.world.Allreduce(MPI.IN_PLACE, max_pd, op=MPI.MAX)
        self.world.Allreduce(MPI.IN_PLACE, sum_sq, op=MPI.SUM)
        self._t_conv_mpi = time.perf_counter() - t_mpi
        arr = np.array([1 if max_pd[0] == 0.0 else 0], dtype="i")
        self._last_global_ratio_primal = float(max_pd[3])
        self._last_global_ratio_dual = float(max_pd[4])
        # Reported alongside the strict per-rank decision; never used to accept a run.
        self._last_boyd = global_boyd_ratios(sum_sq[2:8], abs_tol, rel_tol)
        self._last_boyd_a = consensus_ratios(sum_sq[8:15], abs_tol, rel_tol)
        self._last_boyd_a.update(max_rank_primal_ratio=float(max_pd[5]), max_rank_dual_ratio=float(max_pd[6]))
        self._residual_trace.append((float(self.iteration), float(max_pd[1]), float(max_pd[2]), float(max_pd[3]),
                                     float(max_pd[4]), self._last_boyd["r"], self._last_boyd["s"],
                                     self._last_boyd["primal_ratio"], self._last_boyd["dual_ratio"],
                                     float(max_pd[5]), float(max_pd[6]),
                                     self._last_boyd_a["primal_ratio"], self._last_boyd_a["dual_ratio"]))
        max_pd = max_pd[1:3]

        self._last_global_max_prim = float(max_pd[0])
        self._last_global_max_dual = float(max_pd[1])
        self._last_global_sum_ps = float(sum_sq[0])
        self._last_global_sum_qs = float(sum_sq[1])
        t_console = time.perf_counter()
        if self.rank == 0 and self._log_every > 0 and self.iteration % self._log_every == 0:
            # Diagnose: wie viele Agenten lokal ok?
            print(
                f"[AC GLOBAL] Iter {self.iteration}: "
                f"max_prim={max_pd[0]:.2e} max_dual={max_pd[1]:.2e} "
                f"sum_|ps|={sum_sq[0]:.3f} sum_|qs|={sum_sq[1]:.3f} "
                f"phase={self._slack_phase} all_ok={bool(arr[0])}",
                flush=True,
            )
        self._t_console = time.perf_counter() - t_console
        return bool(arr[0])

    def _gather_global_states(self) -> Optional[Dict[int, Any]]:
        # Nur Float-Dicts (kein AcLocalState-Pickle) — sonst Hang bei 118 Ranks.
        local: Dict[int, Dict[str, float]] = {}
        for bid in self.bus_ids:
            st = self.states[bid]
            local[int(bid)] = {
                "v": float(st.v),
                "va": float(st.va),
                "pg": float(st.pg),
                "qg": float(st.qg),
            }
        gathered = self.world.gather(local, root=0)
        if self.rank != 0 or not gathered:
            return None
        merged: Dict[int, Any] = {}
        for part in gathered:
            if part:
                merged.update(part)
        return merged

    def _update_physics_metrics(self, states: Optional[Dict[int, Any]] = None, already_gathered=False) -> None:
        if self.topology is None:
            return
        if not already_gathered:
            states = self._gather_global_states()
        max_dp = float("inf")
        max_dq = float("inf")
        if self.rank == 0 and states:
            self._validation = validate_ac(self.topology, states,
                AcTolerances(balance=float(self.params.phys_tol)), self.pwl.theta_max)
            max_dp = float(self._validation.get("max_dP", float("inf")))
            max_dq = float(self._validation.get("max_dQ", float("inf")))
            self._validation_ok = self._validation["feasible"]
        self._validation_ok = self.world.bcast(self._validation_ok if self.rank == 0 else None, root=0)
        metrics = np.array([max_dp, max_dq], dtype=float)
        self.world.Bcast(metrics, root=0)
        self._last_phys_max_dp = float(metrics[0])
        self._last_phys_max_dq = float(metrics[1])

    def _maybe_check_physics(self) -> None:
        every = self._phys_check_every
        if every <= 0:
            return
        if self.iteration > 0 and (self.iteration % every) != 0:
            return
        self._update_physics_metrics()
        if self.rank == 0:
            print(
                f"[AC PHYS] Iter {self.iteration}: "
                f"max|dP|={self._last_phys_max_dp:.3e} "
                f"max|dQ|={self._last_phys_max_dq:.3e}",
                flush=True,
            )

    def _phys_ok(self) -> bool:
        tol = float(self.params.phys_tol)
        if not math.isfinite(self._last_phys_max_dp):
            self._update_physics_metrics()
        return self._validation_ok

    def _snapshot_states(self) -> Dict[int, AcLocalState]:
        return {bus_id: copy.deepcopy(self.states[bus_id]) for bus_id in self.bus_ids}

    def _restore_states(self, snapshots: Dict[int, AcLocalState]) -> None:
        for bus_id, snap in snapshots.items():
            self.states[bus_id] = copy.deepcopy(snap)
        if self._single_bus_mode:
            self.state = self.states[self.bus_id]
        self._neighbor_vt_cache = None

    def _maybe_track_best_iterate(self) -> None:
        if not self._track_best:
            return
        max_prim = float(self._last_global_max_prim)
        if max_prim >= self._best_max_prim:
            self._iters_since_best += 1
            return
        self._best_max_prim = max_prim
        self._best_iter = int(self.iteration)
        self._best_sum_ps = float(self._last_global_sum_ps)
        self._best_sum_qs = float(self._last_global_sum_qs)
        self._best_snapshots = self._snapshot_states()
        self._iters_since_best = 0

    def _maybe_accept_best_convergence(self) -> bool:
        """Wenn Best-Iterate schon Tol+Slack erfüllt: dorthin zurück und als konvergiert werten.

        Auf 118 oszilliert der aktuelle Schritt oft über abs_tol, während das Best-Iterate
        darunter liegt — ohne diesen Check bleibt converged=False trotz guter Lösung.
        """
        if self.strict:
            return False
        if not self._track_best or self._best_snapshots is None:
            return False
        if int(self._best_iter) < 0:
            return False
        slack_tol = float(os.getenv("ADMM_SLACK_TOL", "1e-3"))
        hold = int(float(os.getenv("ADMM_BEST_CONV_HOLD", "5")))
        if self._iters_since_best < hold:
            return False
        if float(self._best_max_prim) > float(self.params.abs_tol):
            return False
        if float(self._best_sum_ps) > slack_tol or float(self._best_sum_qs) > slack_tol:
            return False
        if self._phys_gate and self._best_snapshots is not None:
            local_best: Dict[int, Dict[str, float]] = {}
            for bid in self.bus_ids:
                if bid not in self._best_snapshots:
                    continue
                st = self._best_snapshots[bid]
                local_best[int(bid)] = {
                    "v": float(st.v),
                    "va": float(st.va),
                    "pg": float(st.pg),
                    "qg": float(st.qg),
                }
            gathered = self.world.gather(local_best, root=0)
            merged: Optional[Dict[int, Any]] = None
            if self.rank == 0 and gathered:
                merged = {}
                for part in gathered:
                    if part:
                        merged.update(part)
            self._update_physics_metrics(merged if self.rank == 0 else None, already_gathered=True)
            if not self._phys_ok():
                return False
        self._restore_states(self._best_snapshots)
        return True

    def _scale_duals(self, scale_u: float) -> None:
        def _scale_map(d: Optional[Dict[int, float]]) -> Dict[int, float]:
            return {j: scale_u * float(v) for j, v in (d or {}).items()}

        for st in self.states.values():
            st.uv = _scale_map(st.uv)
            st.uth = _scale_map(st.uth)
            st.uv_own = _scale_map(st.uv_own)
            st.uth_own = _scale_map(st.uth_own)
            st.uad = _scale_map(st.uad)
            st.uq = _scale_map(st.uq)
        if not self._single_bus_mode:
            self.u_v = {e: scale_u * float(v) for e, v in (self.u_v or {}).items()}
            self.u_th = {e: scale_u * float(v) for e, v in (self.u_th or {}).items()}
            self.u_v_own = {e: scale_u * float(v) for e, v in (self.u_v_own or {}).items()}
            self.u_th_own = {e: scale_u * float(v) for e, v in (self.u_th_own or {}).items()}

    def _request_model_rebuild(self) -> None:
        # Penalty changes keep the model structure: carry the solver start vector over.
        if self._single_bus_mode:
            model = self.problem._model
            previous = (getattr(model, "_ac_previous_solution", None) if model is not None
                        else getattr(self.problem, "_restored_previous_solution", None))
            self.problem.request_rebuild()
            self.problem._restored_previous_solution = previous
            return
        if self.subnet_problem is not None:
            model = self.subnet_problem._model
            previous = getattr(model, "_ac_previous_solution", None) if model is not None else None
            self.subnet_problem.request_rebuild()
            if previous is not None:
                self.subnet_problem._model._ac_previous_solution = previous
        for prob in self.problems.values():
            prob.request_rebuild()

    def _adaptive_rho_update(self) -> None:
        """Boyd residual balancing on global max/mean of ‖r‖, ‖s‖."""
        if not self._adaptive_rho_enabled:
            return
        self.iters_since_rho_update += 1
        if self.iters_since_rho_update < max(1, self._adaptive_rho_period):
            return
        if self._adaptive_rho_metric == "normalized":
            self._normalized_rho_update()
            return

        local = np.array(
            [float(self._last_global_max_prim), float(self._last_global_max_dual)],
            dtype=float,
        )
        # Already global max from _check_global_convergence; also compute mean via Allreduce
        local_rs = np.array(
            [float(self.state.prim_res), float(self.state.dual_res)],
            dtype=float,
        )
        mean_rs = local_rs.copy()
        self.world.Allreduce(MPI.IN_PLACE, mean_rs, op=MPI.SUM)
        mean_rs /= max(1, self.size)

        if self._adaptive_rho_metric == "mean":
            r_n, s_n = float(mean_rs[0]), float(mean_rs[1])
        else:
            r_n, s_n = float(local[0]), float(local[1])

        if r_n < 1e-12 and s_n < 1e-12:
            return

        rho = float(self.params.rho)
        mu = float(self._adaptive_rho_mu)
        tau = float(self._adaptive_rho_tau)
        new_rho = rho
        if r_n > mu * s_n:
            new_rho = min(self._adaptive_rho_max, rho * tau)
        elif s_n > mu * r_n:
            new_rho = max(self._adaptive_rho_min, rho / tau)
        else:
            return
        if abs(new_rho - rho) < 1e-12:
            return

        scale_u = rho / new_rho
        self._scale_duals(scale_u)
        factor = new_rho / rho
        self.params.rho_v = self.params.effective_rho_v() * factor
        self.params.rho_th = self.params.effective_rho_th() * factor
        self.params.rho = new_rho
        self.rho_updates_total += 1
        self.iters_since_rho_update = 0
        self._request_model_rebuild()
        if self.rank == 0:
            print(
                f"[AC] rho update: {rho:.4g} -> {new_rho:.4g} "
                f"(metric={self._adaptive_rho_metric} r={r_n:.3e} s={s_n:.3e} "
                f"updates={self.rho_updates_total})",
                flush=True,
            )

    def _normalized_rho_update(self) -> None:
        """Residual balancing on the strict convergence quotients (dimensionless, max over ranks).

        Separate ADMM variant: rho_v and rho_theta change by the same factor on all ranks,
        scaled duals are converted as u_new = (rho_old / rho_new) * u_old.
        """
        self.iters_since_rho_update = 0
        if self._adaptive_rho_until > 0 and self.iteration > self._adaptive_rho_until:
            return
        primal, dual = float(self._last_global_ratio_primal), float(self._last_global_ratio_dual)
        rho = float(self.params.rho)
        new_rho = balanced_rho(rho, primal, dual, self._adaptive_rho_mu, self._adaptive_rho_tau,
                               self._adaptive_rho_min, self._adaptive_rho_max)
        if new_rho == rho:
            return
        factor = new_rho / rho
        self._scale_duals(rho / new_rho)
        self.params.rho_v = self.params.effective_rho_v() * factor
        self.params.rho_th = self.params.effective_rho_th() * factor
        self.params.rho = new_rho
        self.rho_updates_total += 1
        self._rho_history.append({"iteration": int(self.iteration), "rho_old": rho, "rho_new": new_rho,
                                  "rho_v": self.params.effective_rho_v(), "rho_theta": self.params.effective_rho_th(),
                                  "primal_ratio": primal, "dual_ratio": dual})
        self._request_model_rebuild()
        if self.rank == 0:
            print(f"[AC] normalized rho update at iter {self.iteration}: {rho:.4g} -> {new_rho:.4g} "
                  f"(primal ratio {primal:.3e}, dual ratio {dual:.3e})", flush=True)

    def _maybe_stall_restart(self) -> None:
        """Nach Stall: Best-Iterate wiederherstellen; rho je nach ADMM_STALL_RHO_ACTION."""
        if self._stall_iters <= 0 or not self._track_best:
            return
        if self._best_snapshots is None:
            return
        if self._iters_since_best < self._stall_iters:
            return
        if float(self._last_global_max_prim) <= float(self.params.abs_tol):
            return

        old_rho = float(self.params.rho)
        old_rv = float(self.params.effective_rho_v())
        old_rth = float(self.params.effective_rho_th())
        # Primäre Skala: max(rho_v, rho_th, rho)
        old_scale = max(old_rho, old_rv, old_rth)
        action = self._stall_rho_action
        if action == "down":
            new_scale = max(self._adaptive_rho_min, old_scale / 2.0)
        elif action == "hold":
            new_scale = old_scale
        else:
            # default "up": mehr Konsensdruck auf hängendem Primärrest
            new_scale = min(self._adaptive_rho_max, max(old_scale * 1.5, old_scale + 1.0))
        factor = new_scale / old_scale if old_scale > 0 else 1.0
        scale_u = 1.0 / factor if factor > 0 else 1.0
        self._restore_states(self._best_snapshots)
        if abs(factor - 1.0) > 1e-12:
            self._scale_duals(scale_u)
            self.params.rho = old_rho * factor
            self.params.rho_v = old_rv * factor
            self.params.rho_th = old_rth * factor
            self._request_model_rebuild()
        self._iters_since_best = 0
        self.iters_since_rho_update = 0
        if self.rank == 0:
            print(
                f"[AC] stall restart from best iter={self._best_iter} "
                f"rho_scale {old_scale:.4g} -> {new_scale:.4g} "
                f"(rv={self.params.effective_rho_v():.4g}, rth={self.params.effective_rho_th():.4g}) "
                f"action={action} (max_prim was {self._last_global_max_prim:.3e})",
                flush=True,
            )

    def _maybe_close_push_rho(self) -> None:
        """Wenn Prim schon nah an abs_tol: einmal ρ erhöhen (letzte Meile)."""
        if self._close_push_done or self._close_push_factor <= 1.0:
            return
        prim = float(self._last_global_max_prim)
        if prim <= 0.0 or prim > self._close_push_band:
            self._close_push_good = 0
            return
        if prim <= float(self.params.abs_tol):
            return
        self._close_push_good += 1
        if self._close_push_good < self._close_push_need:
            return
        self._close_push_done = True
        factor = float(self._close_push_factor)
        old_rv = float(self.params.effective_rho_v())
        old_rth = float(self.params.effective_rho_th())
        self.params.rho = float(self.params.rho) * factor
        self.params.rho_v = old_rv * factor
        self.params.rho_th = old_rth * factor
        self._scale_duals(1.0 / factor)
        self._request_model_rebuild()
        if self.rank == 0:
            print(
                f"[AC] close-push rho x{factor:.3g}: "
                f"rv={old_rv:.4g}->{self.params.effective_rho_v():.4g} "
                f"rth={old_rth:.4g}->{self.params.effective_rho_th():.4g} "
                f"(max_prim={prim:.3e})",
                flush=True,
            )

    def _finalize_best_iterate(self) -> None:
        if self.strict:
            return
        if not self._track_best or self._best_snapshots is None:
            return
        if not self.converged:
            self._restore_states(self._best_snapshots)
        for bus_id in self.bus_ids:
            self._write_log(
                bus_id,
                self._best_snapshots[bus_id],
                extra={
                    "is_best_iterate": True,
                    "best_of_iter": int(self._best_iter),
                    "best_max_prim": float(self._best_max_prim),
                    "converged": bool(self.converged),
                    "convergence_kind": self._convergence_kind,
                    "wall_iteration": int(self.iteration),
                },
            )
        if self.rank == 0:
            cost = sum(float(st.generation_cost) for st in self._best_snapshots.values())
            print(
                f"[AC] Best iterate: iter={self._best_iter} "
                f"max_prim={self._best_max_prim:.2e} "
                f"sum_|ps|={self._best_sum_ps:.3f} sum_|qs|={self._best_sum_qs:.3f} "
                f"generation_cost={cost:.3f}",
                flush=True,
            )

    def _log_convergence_state(self) -> None:
        """Nach best_ok-Restore: finalen konvergierten Zustand ins Agent-Log schreiben."""
        extra = {
            "is_converged": True,
            "converged": True,
            "convergence_kind": self._convergence_kind,
            "wall_iteration": int(self.iteration),
        }
        if self._convergence_kind == "best_iterate":
            extra["best_of_iter"] = int(self._best_iter)
            extra["best_max_prim"] = float(self._best_max_prim)
        for bus_id in self.bus_ids:
            self._write_log(bus_id, self.states[bus_id], extra=extra)

    def _write_run_summary(self) -> None:
        import resource
        logs_dir = pathlib.Path("logs")
        logs_dir.mkdir(exist_ok=True)
        if os.getenv("ADMM_TIMING_TRACE", "0") == "1":
            # Rows = iterations, columns = TIMING_PHASES (seconds).
            np.save(logs_dir / f"timing_rank_{self.rank:06d}.npy",
                    np.column_stack([np.asarray(self._timing[name], dtype=float) for name in TIMING_PHASES]))
        rank_metrics = self.world.gather({"rank": self.rank, "bus_ids": self.bus_ids,
            "cpu_s": time.process_time()-self._cpu_start,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "solver": copy.deepcopy(SOLVER_METRICS),
            "neighbor_ranks": len(self.neighbor_exchange.neighbor_ranks),
            "consensus_edges": int(self.neighbor_exchange.rows),
            "phases": timing_statistics(self._timing),
            "mpi": copy.deepcopy(self.world.metrics)}, root=0)
        if self.rank != 0:
            return
        # Global residual history of every iteration (rank 0; values are global reductions).
        np.save(logs_dir / "residual_trace.npy", np.asarray(self._residual_trace, dtype=float).reshape(-1, len(RESIDUAL_TRACE_COLUMNS)))
        (logs_dir / "residual_trace_columns.json").write_text(json.dumps(RESIDUAL_TRACE_COLUMNS), encoding="utf-8")
        slack_tol = float(os.getenv("ADMM_SLACK_TOL", "1e-3"))
        abs_tol = float(self.params.abs_tol)
        best = None
        if self._best_snapshots is not None and self._best_iter >= 0:
            best = {
                "iteration": int(self._best_iter),
                "max_prim": float(self._best_max_prim),
                "abs_p_slack_sum": float(self._best_sum_ps),
                "abs_q_slack_sum": float(self._best_sum_qs),
                "generation_cost": sum(float(st.generation_cost) for st in self._best_snapshots.values()),
            }
        criteria_met = False
        if best is not None:
            criteria_met = (
                best["max_prim"] <= abs_tol
                and best["abs_p_slack_sum"] <= slack_tol
                and best["abs_q_slack_sum"] <= slack_tol
            )
        summary = {
            "status": "converged" if self.converged and self._validation_ok else "not_converged",
            "raw_ac_feasible": bool(self._validation_ok),
            "postprocessing": "none",
            "cpu_rank_sum_s": sum(r["cpu_s"] for r in rank_metrics),
            "wall_solve_s": self._wall_solve,
            "wall_iterations_s": self._wall_iterations,
            "wall_total_s": self._wall_total,
            "relaxation": float(self.params.relaxation),
            "adaptive_rho": {"enabled": bool(self._adaptive_rho_enabled), "metric": self._adaptive_rho_metric,
                             "mu": self._adaptive_rho_mu, "tau": self._adaptive_rho_tau,
                             "period": self._adaptive_rho_period, "until": self._adaptive_rho_until,
                             "rho_min": self._adaptive_rho_min, "rho_max": self._adaptive_rho_max},
            "final_rho": {"rho": float(self.params.rho), "rho_v": self.params.effective_rho_v(),
                          "rho_theta": self.params.effective_rho_th()},
            "rho_history": self._rho_history,
            "edge_penalty": None if self.edge_penalty is None else self.edge_penalty.summary(),
            "boyd_global_final": self._last_boyd,
            "boyd_consensus_final": self._last_boyd_a,
            "residual_trace": {"file": "logs/residual_trace.npy", "columns": list(RESIDUAL_TRACE_COLUMNS),
                               "rows": len(self._residual_trace)},
            "converged": bool(self.converged),
            "convergence_kind": self._convergence_kind,
            "wall_iterations": int(self.iteration),
            "abs_tol": abs_tol,
            "slack_tol": slack_tol,
            "phys_tol": float(self.params.phys_tol),
            "phys_gate": bool(self._phys_gate),
            "last_phys_max_dP": float(self._last_phys_max_dp),
            "last_phys_max_dQ": float(self._last_phys_max_dq),
            "best_iterate": best,
            "best_iterate_criteria_met": bool(criteria_met),
            "track_best_iterate": bool(self._track_best),
        }
        (logs_dir / "rank_metrics.json").write_text(json.dumps({
            "scope": "agent initialization through final AC validation; excludes launcher and summary gather",
            "phase_semantics": {
                "vt_exchange": "neighbor V/theta exchange before the x-step (only when not cached)",
                "x_solve": "local x-step incl. state bookkeeping and optional solve batching barriers",
                "solve_status": "bus mode: global strict solve-status reduction = waiting for the slowest local solve; 0 in partition mode",
                "consensus_exchange": "neighbor-only exchange of consensus rows; includes waiting for slower neighbors",
                "zu_update": "z- and u-updates and residuals",
                "convergence": "local residual/tolerance computation of the convergence check (excl. reductions and console)",
                "convergence_mpi": "the two global reductions of the convergence check (MAX, SUM), incl. waiting",
                "console": "rank-0 console line of the convergence check (only when log_every > 0)",
                "monitor": "periodic and gate-triggered global AC validation (observation, not an ADMM step)",
                "io": "agent logs and periodic checkpoints"},
            "mpi_wall_semantics": "blocking call elapsed time including waiting; not pure transfer time",
            "byte_semantics": "user buffer extents only; excludes object serialization, MPI protocol and physical network traffic",
            "ranks": rank_metrics}, indent=2), encoding="utf-8")
        path = logs_dir / "admm_run_summary.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        if self._validation is not None:
            (logs_dir / "ac_validation.json").write_text(json.dumps(self._validation, indent=2), encoding="utf-8")
        print(f"[AC] Run summary: converged={self.converged} kind={self._convergence_kind} -> {path}", flush=True)

    def _log_iteration(self) -> None:
        self._write_checkpoint()
        if self._log_every <= 0 or self.iteration % self._log_every:
            return
        for bus_id in self.bus_ids:
            st = self.states[bus_id]
            # Avoid flooding mpirun pipes with 118× prints each iter (can look like EXIT:1).
            if self.rank == 0 and (self.iteration % 10 == 0 or self.iteration <= 2):
                print(
                    f"[AC] Rank {self.rank} Bus {bus_id} Iter {self.iteration:3d} | "
                    f"V={st.v:6.4f} th={st.va:8.4f} Pg={st.pg:8.4f} Qg={st.qg:8.4f} | "
                    f"prim={st.prim_res:.2e} dual={st.dual_res:.2e}",
                    flush=True,
                )
            self._write_log(bus_id, st)

    def _write_checkpoint(self, force=False):
        every = int(os.getenv("ADMM_CHECKPOINT_EVERY", "500"))
        if not force and (every <= 0 or self.iteration % every):
            return
        folder = pathlib.Path("logs/checkpoints") / f"iter_{self.iteration:08d}"
        folder.mkdir(parents=True, exist_ok=True)
        names = ("z_v", "z_th", "z_v_own", "z_th_own", "u_v", "u_th", "u_v_own", "u_th_own")
        payload = {"iteration": self.iteration, "bus_ids": self.bus_ids, "rank": self.rank,
            "single_bus": self._single_bus_mode, "params": asdict(self.params),
            "topology_sha256": hashlib.sha256(pathlib.Path(self.topology_path).read_bytes()).hexdigest(),
            "states": {str(b): asdict(st) for b, st in self.states.items()},
            "consensus": {} if self._single_bus_mode else {
                name: [[i, j, value] for (i, j), value in getattr(self, name).items()] for name in names},
            "edge_penalty": self._edge_config(),
            "warm_start": self._warm_start_payload()}
        target = folder / f"rank_{self.rank:06d}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(target)

    def _restore_checkpoint(self, folder):
        payload = json.loads((folder / f"rank_{self.rank:06d}.json").read_text(encoding="utf-8"))
        if payload["bus_ids"] != self.bus_ids or payload["single_bus"] != self._single_bus_mode:
            raise ValueError("Checkpoint partition does not match current run")
        if payload["topology_sha256"] != hashlib.sha256(pathlib.Path(self.topology_path).read_bytes()).hexdigest():
            raise ValueError("Checkpoint topology does not match current run")
        current = asdict(self.params)
        if any(current[k] != v for k, v in payload["params"].items() if k != "max_iters"):
            raise ValueError("Checkpoint ADMM parameters do not match current run")
        if payload.get("edge_penalty") != self._edge_config():
            raise ValueError("Checkpoint edge-penalty variant does not match current run")
        self.states = {int(b): AcLocalState(**{key: {int(k): v for k, v in val.items()} if isinstance(val, dict) else val
            for key, val in rec.items()}) for b, rec in payload["states"].items()}
        self.state = self.states[self.bus_id]
        for name, values in payload["consensus"].items():
            setattr(self, name, {(int(i), int(j)): float(v) for i, j, v in values})
        self._restore_warm_start(payload.get("warm_start") or {})
        self._neighbor_vt_cache = None
        self.iteration = int(payload["iteration"])
        iterations = self.world.allgather(self.iteration)
        if len(set(iterations)) != 1:
            raise ValueError("Checkpoint iterations differ across ranks")

    def _apply_initial_state(self, path: pathlib.Path) -> None:
        """Start from a given V/theta/Pg/Qg instead of the flat start; z from that state, u = 0."""
        initial = read_initial_state(path)
        for bus_id in self.bus_ids:
            st, src = self.states[bus_id], initial[bus_id]
            st.v, st.va, st.pg, st.qg = src["v"], src["va"], src["pg"], src["qg"]
            st._v_prev, st._va_prev, st._pg_prev = src["v"], src["va"], src["pg"]
        if self._single_bus_mode:
            me, st = self.bus_id, self.states[self.bus_id]
            for j in self.problem.neighbors:
                st.zv[j], st.zth[j] = initial[j]["v"], initial[j]["va"]
                st.zv_own[j], st.zth_own[j] = initial[me]["v"], initial[me]["va"]
            self.state = st
        else:
            for (i, j) in self.cut_edge_lines:
                self.z_v[(i, j)], self.z_th[(i, j)] = initial[j]["v"], initial[j]["va"]
                self.z_v_own[(i, j)], self.z_th_own[(i, j)] = initial[i]["v"], initial[i]["va"]
        self._neighbor_vt_cache = None

    def _edge_factor(self, i: int, j: int) -> float:
        edge = getattr(self, "edge_penalty", None)
        return 1.0 if edge is None else edge(i, j)

    def _edge_config(self) -> Optional[Dict[str, Any]]:
        edge = getattr(self, "edge_penalty", None)
        return None if edge is None else edge.config()

    def _warm_start_payload(self) -> Dict[str, Any]:
        """Solver start data an uninterrupted run would carry into the next local solve."""
        if self._single_bus_mode:
            model = getattr(self.problem, "_model", None)
            pending = getattr(self.problem, "_restored_previous_solution", None)
            previous = getattr(model, "_ac_previous_solution", None) if model is not None else pending
            return {"previous_solution": previous, "last_good": None}
        model = self.subnet_problem._model
        return {"previous_solution": getattr(model, "_ac_previous_solution", None) if model is not None else None,
                "last_good": encode_subnet_result(self.subnet_problem._last_good)}

    def _restore_warm_start(self, warm: Dict[str, Any]) -> None:
        previous = warm.get("previous_solution")
        if previous is not None:
            previous = [float(x) for x in previous]
        if self._single_bus_mode:
            # The local model is built on the first x-step; attach the start vector there.
            self.problem._restored_previous_solution = previous
            return
        if previous is not None:
            self.subnet_problem._model._ac_previous_solution = previous
        self.subnet_problem._last_good = decode_subnet_result(warm.get("last_good"))

    def _publish_live_viz(self, converged: bool = False) -> None:
        try:
            local = build_local_buses_ac(self.bus_ids, self.states)
            topo_name = None
            if getattr(self, "topology_path", None):
                topo_name = str(pathlib.Path(self.topology_path).name)
            publish_live_snapshot(
                self.world,
                self.rank,
                local,
                iteration=int(self.iteration),
                mode="ac",
                topology=topo_name,
                converged=converged,
            )
        except Exception as exc:
            if self.rank == 0:
                print(f"[AC] LIVE_VIZ warn: {exc}", flush=True)

    def _write_log(self, bus_id: int, state: AcLocalState, extra: Optional[Dict] = None) -> None:
        logs_dir = pathlib.Path("logs")
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / f"agent_{bus_id}.jsonl"
        record = {
            "mode": "ac_nonlinear_full" if self.topology.full_ac_model else "ac_pwl",
            "iteration": self.iteration,
            "bus_id": bus_id,
            "rank": self.rank,
            "v": float(state.v),
            "va": float(state.va),
            "pg": float(state.pg),
            "qg": float(state.qg),
            "pij": {str(k): float(v) for k, v in (state.pij or {}).items()},
            "qij": {str(k): float(v) for k, v in (state.qij or {}).items()},
            "prim_res": float(state.prim_res),
            "dual_res": float(state.dual_res),
            "prim_v": float(getattr(state, "prim_v", 0.0)),
            "prim_th": float(getattr(state, "prim_th", 0.0)),
            "generation_cost": float(state.generation_cost),
            "objective_cost": float(state.objective_cost),
            "p_slack": float(state.p_slack),
            "q_slack": float(state.q_slack),
            "timing": getattr(self, "_last_timing", {}),
        }
        if extra:
            record.update(extra)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
