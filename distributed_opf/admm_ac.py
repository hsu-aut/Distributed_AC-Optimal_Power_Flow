"""Local AC-OPF subproblems and ADMM steps (single bus and multi-bus subnets).

For the full model only the exact nonlinear formulation is used (see pwl_ac); the
approximation and slack options serve the legacy simplified cases only.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Set, Tuple

import gurobipy as gp
from gurobipy import GRB

from distributed_opf.admm_params import AdmmParams
from distributed_opf.consensus_penalty import install_centered_penalties, set_consensus_linear, start_centered_penalties
from distributed_opf.edge_penalty import EdgePenalty
from distributed_opf.config import TopologyConfig, line_admittance
from distributed_opf.pwl_ac import (
    AcPwlParams,
    add_apparent_power_polygon,
    add_branch_flow_pwl,
    configure_gurobi_quiet,
)

try:
    from distributed_opf.config import TopologyConfig as _TC
except ImportError:
    _TC = None


SOLVER_METRICS = {"calls": 0, "optimize_wall_s": 0.0, "reported_runtime_s": 0.0, "exceptions": 0, "statuses": {}}


def _strict_solve_accepted(status: int, solution_count: int) -> bool:
    """Require a solved local subproblem, not just a time-limited incumbent."""
    return solution_count > 0 and status in (GRB.OPTIMAL, GRB.LOCALLY_OPTIMAL)


def _optimize_with_wls_retry(model: gp.Model, agent_id: int = 0) -> bool:
    """Soft-stagger + retry Gurobi optimize for Academic WLS token blips.

    Returns False on exhausted license/WLS errors or an unsolved strict
    subproblem. Strict callers propagate failure collectively; an old iterate
    must not be counted as a successful ADMM step.
    """
    try:
        stagger_ms = int(float(os.getenv("ADMM_SOLVE_STAGGER_MS", "50") or "50"))
    except ValueError:
        stagger_ms = 50
    if stagger_ms > 0:
        try:
            rank = int(
                os.getenv("OMPI_COMM_WORLD_RANK")
                or os.getenv("PMI_RANK")
                or os.getenv("MPI_LOCALRANKID")
                or agent_id
                or 0
            )
        except ValueError:
            rank = int(agent_id)
        # Cap stagger so high ranks don't wait tens of seconds each iter
        try:
            cap_ms = int(float(os.getenv("ADMM_SOLVE_STAGGER_CAP_MS", "2000") or "2000"))
        except ValueError:
            cap_ms = 2000
        delay = min(cap_ms, stagger_ms * max(0, rank))
        time.sleep(0.001 * delay)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            model.update()
            if model.Params.OptimalityTarget == 1:
                previous = getattr(model, "_ac_previous_solution", None)
                if previous is not None and len(previous) != model.NumVars:
                    raise RuntimeError(f"Agent {agent_id}: warm start has {len(previous)} values for {model.NumVars} variables")
                for idx, var in enumerate(model.getVars()):
                    start = previous[idx] if previous is not None else var.Start
                    if abs(start) < 1e99:
                        var.PStart = max(var.LB, min(var.UB, start))
                start_centered_penalties(model, previous)
            optimize_start = time.perf_counter()
            SOLVER_METRICS["calls"] += 1
            try:
                model.optimize()
            except Exception:
                SOLVER_METRICS["exceptions"] += 1
                raise
            finally:
                SOLVER_METRICS["optimize_wall_s"] += time.perf_counter()-optimize_start
            SOLVER_METRICS["reported_runtime_s"] += float(model.Runtime)
            status_key = str(int(model.Status))
            SOLVER_METRICS["statuses"][status_key] = SOLVER_METRICS["statuses"].get(status_key, 0)+1
            if os.getenv("ADMM_STRICT", "1") == "1" and not _strict_solve_accepted(int(model.Status), int(model.SolCount)):
                print(f"[AC] Agent {agent_id}: strict subproblem rejected status={model.Status}, solutions={model.SolCount}", flush=True)
                return False
            if model.SolCount > 0:
                model._ac_previous_solution = [float(var.X) for var in model.getVars()]
            return True
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            retryable = any(
                tok in msg for tok in ("license", "token", "wls", "network", "server", "resolve host", "timed out", "code 6")
            )
            print(
                f"[AC] Agent {agent_id}: optimize failed "
                f"(attempt {attempt + 1}/3): {exc}",
                flush=True,
            )
            if not retryable:
                print(
                    f"[AC] Agent {agent_id}: non-retryable optimize error → hold",
                    flush=True,
                )
                return False
            if attempt == 2:
                print(
                    f"[AC] Agent {agent_id}: WLS retries exhausted → hold",
                    flush=True,
                )
                return False
            time.sleep(1.0 + attempt + 0.05 * (int(agent_id) % 17))
    if last_err is not None:
        print(f"[AC] Agent {agent_id}: optimize give up ({last_err}) → hold", flush=True)
    return False


def _new_gurobi_model(name: str, agent_id: int = 0, attempts: int = 6) -> gp.Model:
    """Create a Gurobi model with retries for transient WLS/DNS failures."""
    last_err: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return gp.Model(name)
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            retryable = any(
                tok in msg
                for tok in (
                    "license",
                    "token",
                    "wls",
                    "network",
                    "server",
                    "resolve host",
                    "timed out",
                    "code 6",
                    "connection",
                )
            )
            print(
                f"[AC] Agent {agent_id}: Model() failed "
                f"(attempt {attempt + 1}/{attempts}): {exc}",
                flush=True,
            )
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(2.0 + attempt * 2.0 + 0.1 * (int(agent_id) % 11))
    raise last_err  # pragma: no cover


@dataclass
class AcLocalState:
    v: float = 1.0
    va: float = 0.0
    pg: float = 0.0
    qg: float = 0.0
    pij: Dict[int, float] = field(default_factory=dict)
    qij: Dict[int, float] = field(default_factory=dict)
    # Legacy P/Q duals (unused by V/θ-Konsens; kept for log/compat)
    uad: Dict[int, float] = field(default_factory=dict)
    zad: Dict[int, float] = field(default_factory=dict)
    uq: Dict[int, float] = field(default_factory=dict)
    zq: Dict[int, float] = field(default_factory=dict)
    # V/θ-Konsens: Nachbar-Kopien V_j^i, θ_j^i ↔ Nachbar-eigene Werte
    uv: Dict[int, float] = field(default_factory=dict)
    zv: Dict[int, float] = field(default_factory=dict)
    uth: Dict[int, float] = field(default_factory=dict)
    zth: Dict[int, float] = field(default_factory=dict)
    # V/θ-Konsens: eigene Werte ↔ Nachbar-Kopien von Bus i
    uv_own: Dict[int, float] = field(default_factory=dict)
    zv_own: Dict[int, float] = field(default_factory=dict)
    uth_own: Dict[int, float] = field(default_factory=dict)
    zth_own: Dict[int, float] = field(default_factory=dict)
    v_copy: Dict[int, float] = field(default_factory=dict)
    va_copy: Dict[int, float] = field(default_factory=dict)
    _zv_prev: Dict[int, float] = field(default_factory=dict)
    _zth_prev: Dict[int, float] = field(default_factory=dict)
    _zv_own_prev: Dict[int, float] = field(default_factory=dict)
    _zth_own_prev: Dict[int, float] = field(default_factory=dict)
    _zad_prev: Dict[int, float] = field(default_factory=dict)
    _zq_prev: Dict[int, float] = field(default_factory=dict)
    prim_res: float = 0.0
    dual_res: float = 0.0
    prim_v: float = 0.0
    prim_th: float = 0.0
    _va_prev: float = 0.0
    _v_prev: float = 1.0
    _pg_prev: float = 0.0
    objective_cost: float = 0.0
    p_slack: float = 0.0
    q_slack: float = 0.0
    generation_cost: float = 0.0
    line_slack_max: float = 0.0
    line_slack_mean: float = 0.0
    gen_slack_max: float = 0.0


class AcOpfLocalProblem:
    """Lokales PWL-AC-OPF für einen Bus (Single-Bus-ADMM)."""

    def __init__(
        self,
        *,
        bus_id: int,
        is_generator: bool,
        is_slack: bool,
        pg_bounds: Tuple[float, float],
        qg_bounds: Tuple[float, float],
        v_bounds: Tuple[float, float],
        cost_quad: float,
        cost_lin: float,
        demand_p: float,
        demand_q: float,
        # (line_idx, neighbor_j, r, x, b_shunt, smax) — alle Parallelzweige
        incident_lines: List[Tuple[int, int, float, float, float, float]],
        base_mva: float = 100.0,
        pwl: AcPwlParams | None = None,
        line_to_neighbors: Dict[int, Tuple[float, float, float, float]] | None = None,
        neighbor_v_bounds: Dict[int, Tuple[float, float]] | None = None,
        topology=None,
    ):
        self.bus_id = bus_id
        self.topology = topology
        bus_config = topology.buses[bus_id] if topology is not None else None
        self.shunt_g_pu = bus_config.shunt_g_pu if bus_config else 0.0
        self.shunt_b_pu = bus_config.shunt_b_pu if bus_config else 0.0
        self.fix_slack_voltage = bus_config.fix_slack_voltage if bus_config else True
        self.c0 = bus_config.cost_const if bus_config else 0.0
        self.is_generator = bool(is_generator)
        self.is_slack = bool(is_slack)
        self.pg_min, self.pg_max = pg_bounds
        self.qg_min, self.qg_max = qg_bounds
        self.v_min, self.v_max = v_bounds
        self.c2 = float(cost_quad)
        self.c1 = float(cost_lin)
        self.demand_p = float(demand_p)
        self.demand_q = float(demand_q)
        if incident_lines:
            self.incident_lines = list(incident_lines)
        elif line_to_neighbors:
            # Legacy: ein Zweig pro Nachbar (line_idx = neighbor id)
            self.incident_lines = [
                (j, j, r, x, bsh, smax)
                for j, (r, x, bsh, smax) in sorted(line_to_neighbors.items())
            ]
        else:
            self.incident_lines = []
        self.neighbors = sorted({j for _, j, *_ in self.incident_lines})
        self.line_to_neighbors: Dict[int, Tuple[float, float, float, float]] = {}
        for _ell, j, r, x, bsh, smax in self.incident_lines:
            self.line_to_neighbors[j] = (r, x, bsh, smax)
        self.x_to_neighbors = {j: self.line_to_neighbors[j][1] for j in self.neighbors}  # compat
        self.base_mva = float(base_mva)
        self.pwl = pwl or AcPwlParams.from_env()
        if topology is not None:
            self.pwl.configure_topology(topology)
        self.edge = EdgePenalty.from_env(topology) if topology is not None else None
        self.topology_neighbor_v_bounds = neighbor_v_bounds or {}
        self._model: gp.Model | None = None
        self._vars: Dict = {}
        self._initialized = False
        self._soft_balance_active = False

    def edge_factor(self, j: int) -> float:
        """Penalty factor of the consensus edge to neighbor j (1.0 in standard ADMM)."""
        return 1.0 if self.edge is None else self.edge(self.bus_id, j)

    def request_rebuild(self) -> None:
        if self._model is not None:
            try:
                self._model.dispose()
            except Exception:
                pass
        self._model = None
        self._vars.clear()
        self._initialized = False
        self._soft_balance_active = False
        self._fallback_neigh_target_v = {}
        self._fallback_neigh_target_th = {}
        self._neighbor_v_bounds = {}
        self._neighbor_va_bounds = {}
        self._neighbor_v_fixed = {}
        self._neighbor_va_fixed = {}

    def _build_model_once(self, params: AdmmParams, force_balance_slack: bool = False) -> None:
        if self._initialized and not force_balance_slack:
            return
        if force_balance_slack:
            self.request_rebuild()
        pwl = self.pwl
        rho = float(params.rho)
        model = _new_gurobi_model(f"AC_OPF_Bus_{self.bus_id}", agent_id=self.bus_id)
        configure_gurobi_quiet(model, verbose=os.getenv("GRB_VERBOSE", "0") == "1")

        # Spannung: Betriebsband; Slack-Bus V fix wenn YAML enge Box
        v_lo = float(self.v_min)
        v_hi = float(self.v_max)
        if self.is_slack and abs(v_hi - v_lo) < 1e-6:
            v = model.addVar(lb=v_lo, ub=v_hi, name="V")
        elif self.is_slack and self.fix_slack_voltage:
            v_nom = 1.0
            if v_lo <= v_nom <= v_hi:
                v = model.addVar(lb=v_nom, ub=v_nom, name="V")
            else:
                v = model.addVar(lb=v_lo, ub=v_hi, name="V")
        else:
            v = model.addVar(lb=v_lo, ub=v_hi, name="V")
        s_v_plus = model.addVar(lb=0.0, name="s_v_plus")
        s_v_minus = model.addVar(lb=0.0, name="s_v_minus")
        if pwl.soft_voltage and not (self.is_slack and abs(v_hi - v_lo) < 1e-6):
            model.addConstr(v <= self.v_max + s_v_plus, name="v_ub_soft")
            model.addConstr(-v <= -self.v_min + s_v_minus, name="v_lb_soft")
        else:
            model.addConstr(v <= self.v_max, name="v_ub")
            model.addConstr(-v <= -self.v_min, name="v_lb")

        if self.is_slack:
            theta = model.addVar(lb=0.0, ub=0.0, name="theta")
        else:
            # etwas weitere Box als PWL-Intervall, damit ADMM nicht hart an θ-Wand stößt
            th = 1.25 * float(pwl.theta_max)
            theta = model.addVar(lb=-th, ub=th, name="theta")

        # Generator: immer unbeschränkt + weiche Bounds (wie DC)
        if self.is_generator:
            pg = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name="pg")
            qg = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name="qg")
            s_pg_plus = model.addVar(lb=0.0, name="s_pg_plus")
            s_pg_minus = model.addVar(lb=0.0, name="s_pg_minus")
            s_qg_plus = model.addVar(lb=0.0, name="s_qg_plus")
            s_qg_minus = model.addVar(lb=0.0, name="s_qg_minus")
            model.addConstr(pg <= self.pg_max + s_pg_plus, name="pg_ub")
            model.addConstr(-pg <= -self.pg_min + s_pg_minus, name="pg_lb")
            model.addConstr(qg <= self.qg_max + s_qg_plus, name="qg_ub")
            model.addConstr(-qg <= -self.qg_min + s_qg_minus, name="qg_lb")
        else:
            pg = model.addVar(lb=0.0, ub=0.0, name="pg")
            qg = model.addVar(lb=0.0, ub=0.0, name="qg")
            s_pg_plus = s_pg_minus = s_qg_plus = s_qg_minus = None

        # Bilanz-Slack: force/balance_all → alle Busse; sonst Lastbusse wenn use_slack.
        # Zero-Injection: nicht über Soft-Bilanz, sondern Nachbar-V/θ als ADMM-Variablen
        # (Jacobi-Fix macht ZI-Busse oft überbestimmt → qs bleibt groß).
        demand_q = float(getattr(self, "demand_q", 0.0) or 0.0)
        is_zi = (not self.is_generator) and abs(float(self.demand_p)) <= 1e-12 and abs(demand_q) <= 1e-12
        # Standard-ADMM (Nachbar frei) statt Jacobi: ADMM_JACOBI=0, oder immer für ZI /
        # hochgradige Hub-Busse (sonst hängt 118 an wenigen Knoten >1e-3).
        jacobi = os.getenv("ADMM_JACOBI", "1").strip().lower() not in ("0", "false", "no", "off")
        deg_free = int(float(os.getenv("ADMM_FREE_NBR_DEGREE", "6")))
        is_hub = len(self.neighbors) >= deg_free
        free_nbr = (not jacobi) or is_zi or is_hub
        self._is_zi = is_zi
        self._free_nbr = free_nbr
        use_bal = bool(force_balance_slack) or (
            bool(params.use_slack)
            and (bool(pwl.balance_slack_all_buses) or (not self.is_generator))
        )
        if use_bal:
            p_slack = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name="p_slack")
            q_slack = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name="q_slack")
        else:
            p_slack = model.addVar(lb=0.0, ub=0.0, name="p_slack")
            q_slack = model.addVar(lb=0.0, ub=0.0, name="q_slack")
        bal_p_w = float(params.p_slack_weight)
        bal_q_w = float(pwl.q_slack_weight)
        if force_balance_slack:
            bal_p_w = max(bal_p_w, 1e10)
            bal_q_w = max(bal_q_w, 1e10)

        neigh_v: Dict[int, gp.Var] = {}
        neigh_theta: Dict[int, gp.Var] = {}
        pij: Dict[int, gp.Var] = {}
        qij: Dict[int, gp.Var] = {}
        s_therm: Dict[int, gp.Var] = {}

        nbr_v_bounds: Dict[int, Tuple[float, float]] = {}
        for j in self.neighbors:
            fixed_v = getattr(self, "_neighbor_v_fixed", {}).get(j)
            fixed_t = getattr(self, "_neighbor_va_fixed", {}).get(j)
            v_bounds = getattr(self, "_neighbor_v_bounds", {}).get(j)
            t_bounds = getattr(self, "_neighbor_va_bounds", {}).get(j)
            if fixed_v is not None:
                vj_lb = vj_ub = float(fixed_v)
            elif v_bounds is not None:
                vj_lb, vj_ub = float(v_bounds[0]), float(v_bounds[1])
            else:
                vj_lb, vj_ub = self.topology_neighbor_v_bounds.get(j, (v_lo, v_hi))
            if fixed_t is not None:
                tj_lb = tj_ub = float(fixed_t)
            elif t_bounds is not None:
                tj_lb, tj_ub = float(t_bounds[0]), float(t_bounds[1])
            else:
                th = 1.25 * float(pwl.theta_max)
                tj_lb, tj_ub = -th, th
            nbr_v_bounds[j] = (vj_lb, vj_ub)
            neigh_v[j] = model.addVar(lb=vj_lb, ub=vj_ub, name=f"V_{j}")
            neigh_theta[j] = model.addVar(lb=tj_lb, ub=tj_ub, name=f"theta_{j}")

        for ell, j, r, x, bsh, smax in self.incident_lines:
            fixed_v = getattr(self, "_neighbor_v_fixed", {}).get(j)
            vj_lb, vj_ub = nbr_v_bounds[j]
            pij[ell] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pij_{ell}")
            qij[ell] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qij_{ell}")
            g, b = line_admittance(r, x)
            add_branch_flow_pwl(
                model,
                v_i=v,
                v_j=neigh_v[j],
                theta_i=theta,
                theta_j=neigh_theta[j],
                p_ij=pij[ell],
                q_ij=qij[ell],
                g=g,
                b=b,
                b_shunt=bsh,
                v_i_lb=float(self.v_min),
                v_i_ub=float(self.v_max),
                v_j_lb=vj_lb if fixed_v is None else max(vj_lb, float(self.v_min) - 1e-6),
                v_j_ub=vj_ub if fixed_v is None else min(vj_ub, float(self.v_max) + 1e-6),
                pwl=pwl,
                name=f"br_{self.bus_id}_{ell}_{j}",
                line=self.topology.lines[ell] if self.topology is not None else None,
                from_bus=self.bus_id,
            )
            s_therm[ell] = model.addVar(lb=0.0, name=f"s_therm_{ell}")
            if not pwl.soft_thermal:
                s_therm[ell].UB = 0.0
            add_apparent_power_polygon(
                model, pij[ell], qij[ell], smax, pwl.n_s_poly, f"S_{ell}_{j}", slack=s_therm[ell]
            )

        line_ells = [ell for ell, *_ in self.incident_lines]
        p_sum = gp.quicksum(pij[ell] for ell in line_ells) if line_ells else 0.0
        q_sum = gp.quicksum(qij[ell] for ell in line_ells) if line_ells else 0.0
        model.addConstr(pg - self.demand_p == p_sum + self.shunt_g_pu*v*v + p_slack, name="pbal")
        model.addConstr(qg - self.demand_q == q_sum - self.shunt_b_pu*v*v + q_slack, name="qbal")
        if self.is_generator and not pwl.soft_generation:
            pg.LB, pg.UB = self.pg_min, self.pg_max
            qg.LB, qg.UB = self.qg_min, self.qg_max
            for slack in (s_pg_plus, s_pg_minus, s_qg_plus, s_qg_minus):
                slack.UB = 0.0

        obj = gp.QuadExpr()
        obj += self.c2 * pg * pg + self.c1 * pg + self.c0
        if use_bal:
            obj += bal_p_w * p_slack * p_slack
            if pwl.use_q_slack or force_balance_slack:
                obj += bal_q_w * q_slack * q_slack
        # V/θ-Konsens: eigene Busgrößen; ZI zusätzlich Nachbar-Kopien (nicht Jacobi-fix)
        n_nbr = max(1, len(self.neighbors))
        rho_v = float(params.effective_rho_v())
        rho_th = float(params.effective_rho_th())
        # Edge factors are exactly 1.0 in standard ADMM, so all products below are unchanged there.
        factor = {j: self.edge_factor(j) for j in self.neighbors}
        own_weight = float(n_nbr) if self.edge is None or not self.neighbors else sum(factor[j] for j in self.neighbors)
        obj += 0.5 * rho_v * own_weight * v * v
        if not self.is_slack:
            obj += 0.5 * rho_th * own_weight * theta * theta
        if free_nbr:
            for j in self.neighbors:
                obj += 0.5 * rho_v * factor[j] * neigh_v[j] * neigh_v[j]
                obj += 0.5 * rho_th * factor[j] * neigh_theta[j] * neigh_theta[j]
        if self.is_generator and s_pg_plus is not None:
            w = float(pwl.gen_slack_weight)
            obj += w * (s_pg_plus * s_pg_plus + s_pg_minus * s_pg_minus)
            obj += w * (s_qg_plus * s_qg_plus + s_qg_minus * s_qg_minus)
        if pwl.soft_voltage:
            wv = float(pwl.v_slack_weight)
            obj += wv * (s_v_plus * s_v_plus + s_v_minus * s_v_minus)
        wline = float(pwl.line_slack_weight)
        for ell in s_therm:
            obj += wline * s_therm[ell] * s_therm[ell]

        model.setObjective(obj, GRB.MINIMIZE)
        model.update()

        penalties = [(v, rho_v * own_weight)]
        if not self.is_slack:
            penalties.append((theta, rho_th * own_weight))
        if free_nbr:
            for j in self.neighbors:
                penalties.extend([(neigh_v[j], rho_v * factor[j]), (neigh_theta[j], rho_th * factor[j])])
        install_centered_penalties(model, penalties)

        self._model = model
        self._vars = {
            "v": v,
            "theta": theta,
            "pg": pg,
            "qg": qg,
            "p_slack": p_slack,
            "q_slack": q_slack,
            "neigh_v": neigh_v,
            "neigh_theta": neigh_theta,
            "pij": pij,
            "qij": qij,
            "s_therm": s_therm,
            "s_pg_plus": s_pg_plus,
            "s_pg_minus": s_pg_minus,
            "s_qg_plus": s_qg_plus,
            "s_qg_minus": s_qg_minus,
            "s_v_plus": s_v_plus,
            "s_v_minus": s_v_minus,
            "base_obj": obj,
            "rho": rho,
        }
        self._initialized = True
        self._soft_balance_active = bool(force_balance_slack) or bool(
            params.use_slack and (pwl.balance_slack_all_buses or (not self.is_generator))
        )

    def solve_ac_opf(
        self,
        neighbor_v: Dict[int, float],
        neighbor_va: Dict[int, float],
        u_v: Dict[int, float],
        z_v: Dict[int, float],
        u_th: Dict[int, float],
        z_th: Dict[int, float],
        u_v_own: Dict[int, float],
        z_v_own: Dict[int, float],
        u_th_own: Dict[int, float],
        z_th_own: Dict[int, float],
        params: AdmmParams,
        prev: AcLocalState,
    ):
        # Jacobi: Nachbar-V/θ hart auf Exchange. Modell wiederverwenden (nur Bounds/Obj updaten).
        # Wichtig: Soft-Balance-Fallback nur für eine Iteration — danach wieder hart versuchen
        # (sonst bleibt q_slack dauerhaft > 0 und 118 konvergiert nie wie 3/33).
        if self._soft_balance_active and (not bool(params.use_slack)):
            self.request_rebuild()
        demand_q = float(getattr(self, "demand_q", 0.0) or 0.0)
        is_zi = (not self.is_generator) and abs(float(self.demand_p)) <= 1e-12 and abs(demand_q) <= 1e-12
        jacobi = os.getenv("ADMM_JACOBI", "1").strip().lower() not in ("0", "false", "no", "off")
        deg_free = int(float(os.getenv("ADMM_FREE_NBR_DEGREE", "6")))
        is_hub = len(self.neighbors) >= deg_free
        free_nbr = (not jacobi) or is_zi or is_hub
        need_build = not self._initialized
        if need_build:
            self._neighbor_v_bounds = {}
            self._neighbor_va_bounds = {}
            self._neighbor_v_fixed = {}
            self._neighbor_va_fixed = {}
            for j in self.neighbors:
                if j not in neighbor_v or j not in neighbor_va:
                    raise RuntimeError(f"Bus {self.bus_id}: fehlender Nachbarzustand für {j}")
                if free_nbr:
                    pass
                else:
                    self._neighbor_v_fixed[j] = float(neighbor_v[j])
                    self._neighbor_va_fixed[j] = float(neighbor_va[j])
            self._build_model_once(params)
            restored = getattr(self, "_restored_previous_solution", None)
            if restored is not None:
                # Checkpoint resume: same start vector as the uninterrupted run.
                self._model._ac_previous_solution = list(restored)
                self._restored_previous_solution = None
        model = self._model
        assert model is not None
        rho_v = float(params.effective_rho_v())
        rho_th = float(params.effective_rho_th())
        pij = self._vars["pij"]
        qij = self._vars["qij"]
        neigh_v = self._vars["neigh_v"]
        neigh_theta = self._vars["neigh_theta"]
        v_var = self._vars["v"]
        th_var = self._vars["theta"]

        for j in self.neighbors:
            if j not in neighbor_v or j not in neighbor_va:
                raise RuntimeError(f"Bus {self.bus_id}: fehlender Nachbarzustand für {j}")
            vj = float(neighbor_v[j])
            tj = float(neighbor_va[j])
            if free_nbr:
                pass
            else:
                neigh_v[j].LB = vj
                neigh_v[j].UB = vj
                neigh_theta[j].LB = tj
                neigh_theta[j].UB = tj
                self._neighbor_v_fixed[j] = vj
                self._neighbor_va_fixed[j] = tj

        cv_own = 0.0
        cth_own = 0.0
        for j in self.neighbors:
            cv_own += self.edge_factor(j) * float(z_v_own.get(j, 1.0) - u_v_own.get(j, 0.0))
            cth_own += self.edge_factor(j) * float(z_th_own.get(j, 0.0) - u_th_own.get(j, 0.0))
            if free_nbr:
                set_consensus_linear(model, neigh_v[j], -rho_v * self.edge_factor(j) * float(z_v.get(j, vj) - u_v.get(j, 0.0)))
                set_consensus_linear(model, neigh_theta[j], -rho_th * self.edge_factor(j) * float(z_th.get(j, tj) - u_th.get(j, 0.0)))
            else:
                set_consensus_linear(model, neigh_v[j], 0.0)
                set_consensus_linear(model, neigh_theta[j], 0.0)
        set_consensus_linear(model, v_var, -rho_v * cv_own)
        if not self.is_slack:
            set_consensus_linear(model, th_var, -rho_th * cth_own)

        v_var.Start = float(prev.v)
        th_var.Start = float(prev.va)
        self._vars["pg"].Start = float(prev.pg)
        self._vars["qg"].Start = float(prev.qg)
        for j in self.neighbors:
            neigh_v[j].Start = float(neighbor_v[j])
            neigh_theta[j].Start = float(neighbor_va[j])
        for ell, j, *_ in self.incident_lines:
            if prev.pij and ell in prev.pij:
                pij[ell].Start = float(prev.pij[ell])
            if prev.qij and ell in prev.qij:
                qij[ell].Start = float(prev.qij[ell])

        def _hold_prev():
            self.last_solve_ok = False
            self.solve_failures = getattr(self, "solve_failures", 0) + 1
            print(
                f"[AC] Bus {self.bus_id}: holding previous iterate (WLS/solve fail)",
                flush=True,
            )
            v_copy = (
                dict(prev.v_copy)
                if prev.v_copy
                else {j: float(neighbor_v[j]) for j in self.neighbors}
            )
            va_copy = (
                dict(prev.va_copy)
                if prev.va_copy
                else {j: float(neighbor_va[j]) for j in self.neighbors}
            )
            return (
                float(prev.v),
                float(prev.va),
                float(prev.pg),
                float(prev.qg),
                float(getattr(prev, "objective_cost", 0.0) or 0.0),
                dict(prev.pij or {}),
                dict(prev.qij or {}),
                v_copy,
                va_copy,
                float(getattr(prev, "p_slack", 0.0) or 0.0),
                float(getattr(prev, "q_slack", 0.0) or 0.0),
                float(getattr(prev, "generation_cost", 0.0) or 0.0),
                float(getattr(prev, "line_slack_max", 0.0) or 0.0),
                float(getattr(prev, "line_slack_mean", 0.0) or 0.0),
                float(getattr(prev, "gen_slack_max", 0.0) or 0.0),
            )

        if not _optimize_with_wls_retry(model, self.bus_id):
            return _hold_prev()
        # Strict status acceptance is enforced by _optimize_with_wls_retry.
        # Legacy non-strict mode may continue with a time-limited incumbent.
        _ok = model.status in (GRB.OPTIMAL, GRB.SUBOPTIMAL) or (
            int(getattr(model, "SolCount", 0) or 0) > 0
            and int(model.status) not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED)
        )
        if not _ok:
            # Bounds-Update kann McCormick/PWL inkonsistent machen → einmal neu bauen
            self.request_rebuild()
            if free_nbr:
                self._neighbor_v_fixed = {}
                self._neighbor_va_fixed = {}
            else:
                self._neighbor_v_fixed = {j: float(neighbor_v[j]) for j in self.neighbors}
                self._neighbor_va_fixed = {j: float(neighbor_va[j]) for j in self.neighbors}
            self._build_model_once(params)
            model = self._model
            assert model is not None
            pij = self._vars["pij"]
            qij = self._vars["qij"]
            neigh_v = self._vars["neigh_v"]
            neigh_theta = self._vars["neigh_theta"]
            v_var = self._vars["v"]
            th_var = self._vars["theta"]
            set_consensus_linear(model, v_var, -rho_v * cv_own)
            if not self.is_slack:
                set_consensus_linear(model, th_var, -rho_th * cth_own)
            for j in self.neighbors:
                if free_nbr:
                    vj = float(neighbor_v[j])
                    tj = float(neighbor_va[j])
                    set_consensus_linear(model, neigh_v[j], -rho_v * self.edge_factor(j) * float(z_v.get(j, vj) - u_v.get(j, 0.0)))
                    set_consensus_linear(model, neigh_theta[j], -rho_th * self.edge_factor(j) * float(z_th.get(j, tj) - u_th.get(j, 0.0)))
                else:
                    set_consensus_linear(model, neigh_v[j], 0.0)
                    set_consensus_linear(model, neigh_theta[j], 0.0)
            if not _optimize_with_wls_retry(model, self.bus_id):
                return _hold_prev()
            _ok2 = model.status in (GRB.OPTIMAL, GRB.SUBOPTIMAL) or (
                int(getattr(model, "SolCount", 0) or 0) > 0
                and int(model.status) not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED)
            )
            if not _ok2:
                # Optional: Bilanz-Slack-Fallback (kann Hard-Läufe „weich“ machen)
                allow_slack_fb = os.getenv("ADMM_ALLOW_SLACK_FALLBACK", "1").strip().lower() not in (
                    "0",
                    "false",
                    "no",
                    "off",
                )
                if (not bool(params.use_slack)) and (not allow_slack_fb):
                    return _hold_prev()
                # Harte Nachbar-V/θ können lokal unmachbar sein → Bilanz-Slack erzwingen
                # (Nachbar bleibt Jacobi-fix — sonst leidet der Primär-Konsens stark.)
                self.request_rebuild()
                self._neighbor_v_fixed = {j: float(neighbor_v[j]) for j in self.neighbors}
                self._neighbor_va_fixed = {j: float(neighbor_va[j]) for j in self.neighbors}
                self._build_model_once(params, force_balance_slack=True)
                model = self._model
                assert model is not None
                pij = self._vars["pij"]
                qij = self._vars["qij"]
                neigh_v = self._vars["neigh_v"]
                neigh_theta = self._vars["neigh_theta"]
                v_var = self._vars["v"]
                th_var = self._vars["theta"]
                set_consensus_linear(model, v_var, -rho_v * cv_own)
                if not self.is_slack:
                    set_consensus_linear(model, th_var, -rho_th * cth_own)
                for j in self.neighbors:
                    set_consensus_linear(model, neigh_v[j], 0.0)
                    set_consensus_linear(model, neigh_theta[j], 0.0)
                if not _optimize_with_wls_retry(model, self.bus_id):
                    return _hold_prev()
                _ok3 = model.status in (GRB.OPTIMAL, GRB.SUBOPTIMAL) or (
                    int(getattr(model, "SolCount", 0) or 0) > 0
                    and int(model.status) not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED)
                )
                if not _ok3:
                    return _hold_prev()

        v_val = float(v_var.X)
        self.last_solve_ok = True
        self.last_solver_status = int(model.Status)
        self.last_solver_runtime = float(model.Runtime)
        self.last_solver_gap = None if model.Params.OptimalityTarget == 1 else float(model.MIPGap)
        theta_val = float(th_var.X)
        pg_val = float(self._vars["pg"].X)
        qg_val = float(self._vars["qg"].X)
        p_slack = float(self._vars["p_slack"].X)
        q_slack = float(self._vars["q_slack"].X)
        pij_dict = {ell: float(pij[ell].X) for ell in pij}
        qij_dict = {ell: float(qij[ell].X) for ell in qij}
        if free_nbr:
            v_copy = {j: float(neigh_v[j].X) for j in self.neighbors}
            va_copy = {j: float(neigh_theta[j].X) for j in self.neighbors}
        else:
            v_copy = {j: float(neighbor_v[j]) for j in self.neighbors}
            va_copy = {j: float(neighbor_va[j]) for j in self.neighbors}
        gen_cost = self.c2 * pg_val * pg_val + self.c1 * pg_val + self.c0
        obj = float(model.ObjVal)

        line_slacks = [float(svar.X) for svar in self._vars["s_therm"].values()]
        line_s_max = max(line_slacks) if line_slacks else 0.0
        line_s_mean = (sum(line_slacks) / len(line_slacks)) if line_slacks else 0.0
        gen_s_max = 0.0
        if self.is_generator and self._vars["s_pg_plus"] is not None:
            gen_s_max = max(
                float(self._vars["s_pg_plus"].X),
                float(self._vars["s_pg_minus"].X),
                float(self._vars["s_qg_plus"].X),
                float(self._vars["s_qg_minus"].X),
            )

        return (
            v_val,
            theta_val,
            pg_val,
            qg_val,
            obj,
            pij_dict,
            qij_dict,
            v_copy,
            va_copy,
            p_slack,
            q_slack,
            gen_cost,
            line_s_max,
            line_s_mean,
            gen_s_max,
        )


def admm_x_step_ac(
    problem: AcOpfLocalProblem,
    state: AcLocalState,
    neighbor_states: Dict[int, AcLocalState],
    params: AdmmParams,
) -> Tuple:
    neighbor_v = {j: neighbor_states[j].v for j in problem.neighbors if j in neighbor_states}
    neighbor_va = {j: neighbor_states[j].va for j in problem.neighbors if j in neighbor_states}
    return problem.solve_ac_opf(
        neighbor_v=neighbor_v,
        neighbor_va=neighbor_va,
        u_v=state.uv or {},
        z_v=state.zv or {},
        u_th=state.uth or {},
        z_th=state.zth or {},
        u_v_own=state.uv_own or {},
        z_v_own=state.zv_own or {},
        u_th_own=state.uth_own or {},
        z_th_own=state.zth_own or {},
        params=params,
        prev=state,
    )


def admm_z_step_ac(
    problem: AcOpfLocalProblem,
    local_after_x: AcLocalState,
    neighbor_states_after_x: Dict[int, AcLocalState],
    params: AdmmParams,
) -> Tuple[Dict[int, float], Dict[int, float], Dict[int, float], Dict[int, float], float]:
    """
    Exact quadratic consensus minimizer for both endpoint occurrences.
    Angles are real variables, consistent with the x-step's quadratic penalty;
    wrapping by 2*pi would change this minimizer and mask residuals.
    """
    alpha = float(getattr(params, "relaxation", 1.0))
    new_zv: Dict[int, float] = {}
    new_zth: Dict[int, float] = {}
    new_zv_own: Dict[int, float] = {}
    new_zth_own: Dict[int, float] = {}
    s2_v = 0.0
    s2_th = 0.0
    zv_prev = dict(local_after_x.zv or {})
    zth_prev = dict(local_after_x.zth or {})
    zv_own_prev = dict(local_after_x.zv_own or {})
    zth_own_prev = dict(local_after_x.zth_own or {})

    v_i = float(local_after_x.v)
    th_i = float(local_after_x.va)
    v_i_prev = float(local_after_x._v_prev)
    th_i_prev = float(local_after_x._va_prev)

    for j in problem.neighbors:
        v_j_used = float((local_after_x.v_copy or {}).get(j, 1.0))
        th_j_used = float((local_after_x.va_copy or {}).get(j, 0.0))
        neigh = neighbor_states_after_x.get(j)
        if neigh is None:
            if os.getenv("ADMM_STRICT", "0") == "1":
                raise RuntimeError(f"Missing consensus state for neighbor {j}")
            v_j = v_j_used
            th_j = th_j_used
            v_i_at_j = v_i_prev
            th_i_at_j = th_i_prev
            u_v_j_at_j = 0.0
            u_th_j_at_j = 0.0
            u_v_i_at_j = 0.0
            u_th_i_at_j = 0.0
        else:
            v_j = float(neigh.v)
            th_j = float(neigh.va)
            # The neighboring x-step optimizes a copy of our own voltage.
            v_i_at_j = float((neigh.v_copy or {}).get(problem.bus_id, v_i_prev))
            th_i_at_j = float((neigh.va_copy or {}).get(problem.bus_id, th_i_prev))
            u_v_j_at_j = float((neigh.uv_own or {}).get(problem.bus_id, 0.0))
            u_th_j_at_j = float((neigh.uth_own or {}).get(problem.bus_id, 0.0))
            u_v_i_at_j = float((neigh.uv or {}).get(problem.bus_id, 0.0))
            u_th_i_at_j = float((neigh.uth or {}).get(problem.bus_id, 0.0))

        u_v_j = float((local_after_x.uv or {}).get(j, 0.0))
        u_th_j = float((local_after_x.uth or {}).get(j, 0.0))
        u_v_own_j = float((local_after_x.uv_own or {}).get(j, 0.0))
        u_th_own_j = float((local_after_x.uth_own or {}).get(j, 0.0))

        zv_old = float(zv_prev.get(j, v_j_used))
        zth_old = float(zth_prev.get(j, th_j_used))
        zv_own_old = float(zv_own_prev.get(j, v_i_prev))
        zth_own_old = float(zth_own_prev.get(j, th_i_prev))

        if alpha != 1.0:
            v_j_used_h = alpha * v_j_used + (1.0 - alpha) * zv_old
            th_j_used_h = alpha * th_j_used + (1.0 - alpha) * zth_old
            v_j_h = alpha * v_j + (1.0 - alpha) * zv_old
            th_j_h = alpha * th_j + (1.0 - alpha) * zth_old
            v_i_h = alpha * v_i + (1.0 - alpha) * zv_own_old
            th_i_h = alpha * th_i + (1.0 - alpha) * zth_own_old
            v_i_at_j_h = alpha * v_i_at_j + (1.0 - alpha) * zv_own_old
            th_i_at_j_h = alpha * th_i_at_j + (1.0 - alpha) * zth_own_old
        else:
            v_j_used_h, th_j_used_h = v_j_used, th_j_used
            v_j_h, th_j_h = v_j, th_j
            v_i_h, th_i_h = v_i, th_i
            v_i_at_j_h, th_i_at_j_h = v_i_at_j, th_i_at_j

        zv = 0.5 * ((v_j_used_h + u_v_j) + (v_j_h + u_v_j_at_j))
        zth = 0.5 * ((th_j_used_h + u_th_j) + (th_j_h + u_th_j_at_j))
        zv_own = 0.5 * ((v_i_h + u_v_own_j) + (v_i_at_j_h + u_v_i_at_j))
        zth_own = 0.5 * ((th_i_h + u_th_own_j) + (th_i_at_j_h + u_th_i_at_j))
        new_zv[j] = zv
        new_zth[j] = zth
        new_zv_own[j] = zv_own
        new_zth_own[j] = zth_own
        edge = getattr(problem, "edge", None)
        weight = 1.0 if edge is None else edge(problem.bus_id, j) ** 2
        s2_v += weight * ((zv - zv_old) ** 2 + (zv_own - zv_own_old) ** 2)
        s2_th += weight * ((zth - zth_old) ** 2 + (zth_own - zth_own_old) ** 2)

    rho_v = float(params.effective_rho_v())
    rho_th = float(params.effective_rho_th())
    dual_res = math.sqrt((rho_v * rho_v) * s2_v + (rho_th * rho_th) * s2_th)
    return new_zv, new_zth, new_zv_own, new_zth_own, dual_res


def admm_u_step_ac(
    problem: AcOpfLocalProblem,
    local_after_x: AcLocalState,
    z_v: Dict[int, float],
    z_th: Dict[int, float],
    z_v_own: Dict[int, float],
    z_th_own: Dict[int, float],
    params: AdmmParams,
) -> AcLocalState:
    new_uv: Dict[int, float] = {}
    new_uth: Dict[int, float] = {}
    new_uv_own: Dict[int, float] = {}
    new_uth_own: Dict[int, float] = {}
    r2_v = 0.0
    r2_th = 0.0
    alpha = float(getattr(params, "relaxation", 1.0))
    v_i = float(local_after_x.v)
    th_i = float(local_after_x.va)
    for j in problem.neighbors:
        v_j_used = float((local_after_x.v_copy or {}).get(j, 1.0))
        th_j_used = float((local_after_x.va_copy or {}).get(j, 0.0))
        zv = float(z_v.get(j, v_j_used))
        zth = float(z_th.get(j, th_j_used))
        zv_own = float(z_v_own.get(j, v_i))
        zth_own = float(z_th_own.get(j, th_i))
        rv = v_j_used - zv
        rth = th_j_used - zth
        rv_own = v_i - zv_own
        rth_own = th_i - zth_own
        r2_v += rv * rv + rv_own * rv_own
        r2_th += rth * rth + rth_own * rth_own
        if alpha != 1.0:
            # Relaxed ADMM: u += x_hat - z with the same x_hat as in the z-step (old z from local_after_x).
            hv = alpha * v_j_used + (1.0 - alpha) * float((local_after_x.zv or {}).get(j, v_j_used))
            hth = alpha * th_j_used + (1.0 - alpha) * float((local_after_x.zth or {}).get(j, th_j_used))
            hv_own = alpha * v_i + (1.0 - alpha) * float((local_after_x.zv_own or {}).get(j, local_after_x._v_prev))
            hth_own = alpha * th_i + (1.0 - alpha) * float((local_after_x.zth_own or {}).get(j, local_after_x._va_prev))
            uv_step, uth_step, uv_own_step, uth_own_step = hv - zv, hth - zth, hv_own - zv_own, hth_own - zth_own
        else:
            uv_step, uth_step, uv_own_step, uth_own_step = rv, rth, rv_own, rth_own
        new_uv[j] = float((local_after_x.uv or {}).get(j, 0.0)) + uv_step
        new_uth[j] = float((local_after_x.uth or {}).get(j, 0.0)) + uth_step
        new_uv_own[j] = float((local_after_x.uv_own or {}).get(j, 0.0)) + uv_own_step
        new_uth_own[j] = float((local_after_x.uth_own or {}).get(j, 0.0)) + uth_own_step

    return AcLocalState(
        v=local_after_x.v,
        va=local_after_x.va,
        pg=local_after_x.pg,
        qg=local_after_x.qg,
        pij=dict(local_after_x.pij or {}),
        qij=dict(local_after_x.qij or {}),
        uad=dict(local_after_x.uad or {}),
        zad=dict(local_after_x.zad or {}),
        uq=dict(local_after_x.uq or {}),
        zq=dict(local_after_x.zq or {}),
        uv=new_uv,
        zv=dict(z_v),
        uth=new_uth,
        zth=dict(z_th),
        uv_own=new_uv_own,
        zv_own=dict(z_v_own),
        uth_own=new_uth_own,
        zth_own=dict(z_th_own),
        v_copy=dict(local_after_x.v_copy or {}),
        va_copy=dict(local_after_x.va_copy or {}),
        _zv_prev=dict(local_after_x.zv or {}),
        _zth_prev=dict(local_after_x.zth or {}),
        _zv_own_prev=dict(local_after_x.zv_own or {}),
        _zth_own_prev=dict(local_after_x.zth_own or {}),
        prim_res=math.sqrt(r2_v + r2_th),
        dual_res=float(local_after_x.dual_res),
        prim_v=math.sqrt(r2_v),
        prim_th=math.sqrt(r2_th),
        _va_prev=local_after_x._va_prev,
        _v_prev=local_after_x._v_prev,
        _pg_prev=local_after_x._pg_prev,
        objective_cost=local_after_x.objective_cost,
        p_slack=local_after_x.p_slack,
        q_slack=local_after_x.q_slack,
        generation_cost=local_after_x.generation_cost,
        line_slack_max=local_after_x.line_slack_max,
        line_slack_mean=local_after_x.line_slack_mean,
        gen_slack_max=local_after_x.gen_slack_max,
    )


class AcSubnetResult(NamedTuple):
    v: Dict[int, float]
    va: Dict[int, float]
    pg: Dict[int, float]
    qg: Dict[int, float]
    pij: Dict[Tuple[int, int, int], float]
    qij: Dict[Tuple[int, int, int], float]
    objective_cost: float
    generation_cost: float
    p_slack: Dict[int, float]
    q_slack: Dict[int, float]
    line_slack_max: float
    line_slack_mean: float
    gen_slack_max: float
    v_copy: Dict[int, float]
    va_copy: Dict[int, float]


def encode_subnet_result(result: AcSubnetResult | None):
    """JSON form of a subnet result; tuple-keyed branch flows become [ell, i, j, value] rows."""
    if result is None:
        return None
    data = result._asdict()
    for name in ("pij", "qij"):
        data[name] = [[ell, i, j, value] for (ell, i, j), value in data[name].items()]
    for name in ("v", "va", "pg", "qg", "p_slack", "q_slack", "v_copy", "va_copy"):
        data[name] = [[bus, value] for bus, value in data[name].items()]
    return data


def decode_subnet_result(data) -> AcSubnetResult | None:
    if data is None:
        return None
    fields = dict(data)
    for name in ("pij", "qij"):
        fields[name] = {(int(ell), int(i), int(j)): float(value) for ell, i, j, value in fields[name]}
    for name in ("v", "va", "pg", "qg", "p_slack", "q_slack", "v_copy", "va_copy"):
        fields[name] = {int(bus): float(value) for bus, value in fields[name]}
    return AcSubnetResult(**fields)


class AcOpfSubnetProblem:
    """Multi-bus AC subproblem with boundary consensus on voltage and angle."""

    def __init__(
        self,
        agent_id: int,
        bus_ids: List[int],
        topology: TopologyConfig,
        params: AdmmParams,
        cut_edges: Dict[int, List[Tuple[int, int]]],
        pwl: AcPwlParams | None = None,
    ):
        self.agent_id = agent_id
        self.bus_ids = sorted(bus_ids)
        self.bus_id_set = set(self.bus_ids)
        self.topology = topology
        self.params = params
        self.pwl = pwl or AcPwlParams.from_env()
        self.pwl.configure_topology(topology)
        self.edge = EdgePenalty.from_env(topology)
        self.cut_edges = cut_edges

        self.cut_edge_lines: List[Tuple[int, int]] = []
        seen: Set[Tuple[int, int]] = set()
        for edges in cut_edges.values():
            for (i, j) in edges:
                if (i, j) not in seen:
                    seen.add((i, j))
                    self.cut_edge_lines.append((i, j))

        self.internal_lines: List[Tuple[int, int, int]] = []
        self.boundary_lines: List[Tuple[int, int, int]] = []
        for ell, line in enumerate(topology.lines):
            i, j = line.from_bus, line.to_bus
            if i in self.bus_id_set and j in self.bus_id_set:
                self.internal_lines.append((ell, i, j))
            elif (i in self.bus_id_set) != (j in self.bus_id_set):
                self.boundary_lines.append((ell, i, j) if i in self.bus_id_set else (ell, j, i))

        self._model: gp.Model | None = None
        self._vars: Dict = {}
        self._initialized = False
        self._last_good: AcSubnetResult | None = None
        self._build_model_once()

    def request_rebuild(self) -> None:
        if self._model is not None:
            try:
                self._model.dispose()
            except Exception:
                pass
        self._model = None
        self._vars.clear()
        self._initialized = False
        self._build_model_once()

    def edge_factor(self, i: int, j: int) -> float:
        """Penalty factor of consensus edge i-j (1.0 in standard ADMM)."""
        return 1.0 if self.edge is None else self.edge(i, j)

    def _line_params(self, i: int, j: int) -> Tuple[float, float, float, float]:
        for line in self.topology.lines:
            if (line.from_bus == i and line.to_bus == j) or (line.from_bus == j and line.to_bus == i):
                return line.r_pu, line.x_pu, line.b_shunt_pu, line.thermal_limit_pu
        raise KeyError(f"Linie {i}-{j} nicht in Topologie")

    def _build_model_once(self) -> None:
        if self._initialized:
            return
        pwl = self.pwl
        params = self.params
        rho = float(params.rho)
        rho_v = float(params.effective_rho_v())
        rho_th = float(params.effective_rho_th())
        model = _new_gurobi_model(f"AC_Subnet_{self.agent_id}", agent_id=self.agent_id)
        configure_gurobi_quiet(model, verbose=os.getenv("GRB_VERBOSE", "0") == "1")
        threads = int(float(os.getenv("GRB_THREADS", "1") or "1"))
        if threads > 0:
            model.setParam(GRB.Param.Threads, threads)
        # PWL/SOS und bilineare Produkte benoetigen NonConvex.
        model.setParam(GRB.Param.NonConvex, 2)
        tl = os.getenv("GRB_TIMELIMIT")
        if tl not in (None, ""):
            try:
                tlf = float(tl)
            except Exception:
                tlf = 0.0
            if tlf > 0:
                model.setParam(GRB.Param.TimeLimit, tlf)

        v: Dict[int, gp.Var] = {}
        theta: Dict[int, gp.Var] = {}
        pg: Dict[int, gp.Var] = {}
        qg: Dict[int, gp.Var] = {}
        p_slack: Dict[int, gp.Var] = {}
        q_slack: Dict[int, gp.Var] = {}
        self._vars = {"s_therm": {}, "s_v": {}, "s_gen": {}}

        for bus_id in self.bus_ids:
            bus = self.topology.buses[bus_id]
            # Betriebsband hart für gültige McCormick-/Bilinear-Hülle
            v_lo, v_hi = float(bus.v_min_pu), float(bus.v_max_pu)
            v[bus_id] = model.addVar(lb=v_lo, ub=v_hi, name=f"V_{bus_id}")
            if bus.is_slack and bus.fix_slack_voltage and v_hi-v_lo >= 1e-6 and v_lo <= 1.0 <= v_hi:
                v[bus_id].LB = v[bus_id].UB = 1.0
            s_v_plus = model.addVar(lb=0.0, name=f"sv_p_{bus_id}")
            s_v_minus = model.addVar(lb=0.0, name=f"sv_m_{bus_id}")
            if pwl.soft_voltage:
                model.addConstr(v[bus_id] <= bus.v_max_pu + s_v_plus, name=f"vub_{bus_id}")
                model.addConstr(-v[bus_id] <= -bus.v_min_pu + s_v_minus, name=f"vlb_{bus_id}")
            else:
                model.addConstr(v[bus_id] <= bus.v_max_pu, name=f"vub_{bus_id}")
                model.addConstr(-v[bus_id] <= -bus.v_min_pu, name=f"vlb_{bus_id}")
            self._vars.setdefault("s_v", {})[bus_id] = (s_v_plus, s_v_minus)

            if bus.is_slack:
                theta[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"theta_{bus_id}")
            else:
                th = 1.25 * float(pwl.theta_max)
                theta[bus_id] = model.addVar(lb=-th, ub=th, name=f"theta_{bus_id}")
            if bus.is_generator:
                pg[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pg_{bus_id}")
                qg[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qg_{bus_id}")
                s_pg_p = model.addVar(lb=0.0, name=f"spgp_{bus_id}")
                s_pg_m = model.addVar(lb=0.0, name=f"spgm_{bus_id}")
                s_qg_p = model.addVar(lb=0.0, name=f"sqgp_{bus_id}")
                s_qg_m = model.addVar(lb=0.0, name=f"sqgm_{bus_id}")
                model.addConstr(pg[bus_id] <= bus.pg_max + s_pg_p, name=f"pgub_{bus_id}")
                model.addConstr(-pg[bus_id] <= -bus.pg_min + s_pg_m, name=f"pglb_{bus_id}")
                model.addConstr(qg[bus_id] <= bus.qg_max + s_qg_p, name=f"qgub_{bus_id}")
                model.addConstr(-qg[bus_id] <= -bus.qg_min + s_qg_m, name=f"qglb_{bus_id}")
                self._vars.setdefault("s_gen", {})[bus_id] = (s_pg_p, s_pg_m, s_qg_p, s_qg_m)
                if not pwl.soft_generation:
                    pg[bus_id].LB, pg[bus_id].UB = bus.pg_min, bus.pg_max
                    qg[bus_id].LB, qg[bus_id].UB = bus.qg_min, bus.qg_max
                    for slack in self._vars["s_gen"][bus_id]:
                        slack.UB = 0.0
            else:
                pg[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"pg_{bus_id}")
                qg[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"qg_{bus_id}")
            use_bal = bool(params.use_slack) and (
                bus.is_slack or bool(pwl.balance_slack_all_buses)
            )
            if use_bal:
                p_slack[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"ps_{bus_id}")
                q_slack[bus_id] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qs_{bus_id}")
            else:
                p_slack[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"ps_{bus_id}")
                q_slack[bus_id] = model.addVar(lb=0.0, ub=0.0, name=f"qs_{bus_id}")

        # Boundary neighbor voltage/angle (ADMM-Kopien, Betriebsband)
        boundary_buses: Set[int] = set()
        for (i, j) in self.cut_edge_lines:
            if i in self.bus_id_set and j not in self.bus_id_set:
                boundary_buses.add(j)
            elif j in self.bus_id_set and i not in self.bus_id_set:
                boundary_buses.add(i)

        neigh_v: Dict[int, gp.Var] = {}
        neigh_theta: Dict[int, gp.Var] = {}
        for jb in boundary_buses:
            th = 1.25 * float(pwl.theta_max)
            # Bounds vom Topologie-Bus falls bekannt, sonst Betriebs-Default
            if jb in self.topology.buses:
                bjb = self.topology.buses[jb]
                vj_lo, vj_hi = float(bjb.v_min_pu), float(bjb.v_max_pu)
            else:
                vj_lo, vj_hi = 0.9, 1.1
            neigh_v[jb] = model.addVar(lb=vj_lo, ub=vj_hi, name=f"Vext_{jb}")
            neigh_theta[jb] = model.addVar(lb=-th, ub=th, name=f"text_{jb}")

        pij: Dict[Tuple[int, int], gp.Var] = {}
        qij: Dict[Tuple[int, int], gp.Var] = {}

        def _add_dir_flow(ell: int, i: int, j: int, v_i, v_j, t_i, t_j, v_i_b, v_j_b):
            line = self.topology.lines[ell]
            r, x, bsh, smax = line.r_pu, line.x_pu, line.b_shunt_pu, line.thermal_limit_pu
            g, b = line_admittance(r, x)
            key = (ell, i, j)
            pij[key] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"pij_{ell}_{i}_{j}")
            qij[key] = model.addVar(lb=-GRB.INFINITY, ub=GRB.INFINITY, name=f"qij_{ell}_{i}_{j}")
            add_branch_flow_pwl(
                model,
                v_i=v_i,
                v_j=v_j,
                theta_i=t_i,
                theta_j=t_j,
                p_ij=pij[key],
                q_ij=qij[key],
                g=g,
                b=b,
                b_shunt=bsh,
                v_i_lb=v_i_b[0],
                v_i_ub=v_i_b[1],
                v_j_lb=v_j_b[0],
                v_j_ub=v_j_b[1],
                pwl=pwl,
                name=f"br_{ell}_{i}_{j}",
                line=line, from_bus=i,
            )
            s = model.addVar(lb=0.0, ub=GRB.INFINITY if pwl.soft_thermal else 0., name=f"st_{ell}_{i}_{j}")
            add_apparent_power_polygon(
                model, pij[key], qij[key], smax, pwl.n_s_poly, f"S_{ell}_{i}_{j}", slack=s
            )
            self._vars.setdefault("s_therm", {})[key] = s

        # self._vars already initialized above
        for (ell, i, j) in self.internal_lines:
            bi, bj = self.topology.buses[i], self.topology.buses[j]
            vi_b = (float(bi.v_min_pu), float(bi.v_max_pu))
            vj_b = (float(bj.v_min_pu), float(bj.v_max_pu))
            _add_dir_flow(
                ell, i, j, v[i], v[j], theta[i], theta[j],
                vi_b, vj_b,
            )
            _add_dir_flow(
                ell, j, i, v[j], v[i], theta[j], theta[i],
                vj_b, vi_b,
            )

        for (ell, i, j) in self.boundary_lines:
            # i lokal, j remote (laut Agent-Konvention)
            if i not in self.bus_id_set:
                i, j = j, i
            bi = self.topology.buses[i]
            vi_b = (float(bi.v_min_pu), float(bi.v_max_pu))
            if j in self.topology.buses:
                bj = self.topology.buses[j]
                vj_b = (float(bj.v_min_pu), float(bj.v_max_pu))
            else:
                vj_b = (0.9, 1.1)
            _add_dir_flow(
                ell, i, j, v[i], neigh_v[j], theta[i], neigh_theta[j],
                vi_b, vj_b,
            )

        for bus_id in self.bus_ids:
            bus = self.topology.buses[bus_id]
            p_sum = gp.LinExpr()
            q_sum = gp.LinExpr()
            for (ell, a, b), pvar in pij.items():
                if a == bus_id:
                    p_sum += pvar
                    q_sum += qij[(ell, a, b)]
            model.addConstr(pg[bus_id] - bus.demand_p == p_sum + bus.shunt_g_pu*v[bus_id]*v[bus_id] + p_slack[bus_id], name=f"pbal_{bus_id}")
            model.addConstr(qg[bus_id] - bus.demand_q == q_sum - bus.shunt_b_pu*v[bus_id]*v[bus_id] + q_slack[bus_id], name=f"qbal_{bus_id}")

        obj = gp.QuadExpr()
        for bus_id in self.bus_ids:
            bus = self.topology.buses[bus_id]
            if bus.is_generator:
                obj += bus.cost_quad * pg[bus_id] * pg[bus_id] + bus.cost_lin * pg[bus_id] + bus.cost_const
            use_bal = bool(params.use_slack) and (
                bus.is_slack or bool(pwl.balance_slack_all_buses)
            )
            if use_bal:
                obj += float(params.p_slack_weight) * p_slack[bus_id] * p_slack[bus_id]
                obj += float(pwl.q_slack_weight) * q_slack[bus_id] * q_slack[bus_id]
            if pwl.soft_voltage and bus_id in self._vars.get("s_v", {}):
                svp, svm = self._vars["s_v"][bus_id]
                wv = float(pwl.v_slack_weight)
                obj += wv * (svp * svp + svm * svm)
            if bus.is_generator and bus_id in self._vars.get("s_gen", {}):
                spgp, spgm, sqgp, sqgm = self._vars["s_gen"][bus_id]
                wg = float(pwl.gen_slack_weight)
                obj += wg * (spgp * spgp + spgm * spgm + sqgp * sqgp + sqgm * sqgm)
        # V/θ-Konsens auf Cut-Edges (keine P/Q-Antisymmetrie — Verluste erlaubt)
        cut_local_buses: Set[int] = set()
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            cut_local_buses.add(ii)
            # Nachbar-Kopie: 0.5 rho Vj² / θj² (Linearterm in solve)
            factor = self.edge_factor(ii, jj)
            obj += 0.5 * rho_v * factor * neigh_v[jj] * neigh_v[jj]
            obj += 0.5 * rho_th * factor * neigh_theta[jj] * neigh_theta[jj]
        own_weights: Dict[int, float] = {}
        for bi in cut_local_buses:
            n_cuts = sum(
                1
                for (i, j) in self.cut_edge_lines
                if (i == bi and j not in self.bus_id_set)
                or (j == bi and i not in self.bus_id_set)
            )
            n_cuts = max(1, n_cuts)
            if self.edge is None:
                own_weight = float(n_cuts)
            else:
                own_weight = sum(self.edge_factor(*((i, j) if i == bi else (j, i)))
                                 for (i, j) in self.cut_edge_lines
                                 if (i == bi and j not in self.bus_id_set) or (j == bi and i not in self.bus_id_set))
            own_weights[bi] = own_weight
            obj += 0.5 * rho_v * own_weight * v[bi] * v[bi]
            bus = self.topology.buses[bi]
            if not bus.is_slack:
                obj += 0.5 * rho_th * own_weight * theta[bi] * theta[bi]
        wline = float(pwl.line_slack_weight)
        for s in self._vars["s_therm"].values():
            obj += wline * s * s

        model.setObjective(obj, GRB.MINIMIZE)
        model.update()
        penalties = []
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            factor = self.edge_factor(ii, jj)
            penalties.extend([(v[ii], rho_v * factor), (neigh_v[jj], rho_v * factor), (neigh_theta[jj], rho_th * factor)])
            if not self.topology.buses[ii].is_slack:
                penalties.append((theta[ii], rho_th * factor))
        install_centered_penalties(model, penalties)
        self._model = model
        self._vars.update({
            "v": v,
            "theta": theta,
            "pg": pg,
            "qg": qg,
            "p_slack": p_slack,
            "q_slack": q_slack,
            "neigh_v": neigh_v,
            "neigh_theta": neigh_theta,
            "pij": pij,
            "qij": qij,
            "rho": rho,
            "cut_local_buses": cut_local_buses,
            "boundary_buses": boundary_buses,
        })
        self._initialized = True

    def solve(
        self,
        neighbor_theta: Dict[int, Dict[int, float]],
        neighbor_v: Dict[int, Dict[int, float]],
        z_v: Dict[Tuple[int, int], float],
        u_v: Dict[Tuple[int, int], float],
        z_th: Dict[Tuple[int, int], float],
        u_th: Dict[Tuple[int, int], float],
        z_v_own: Dict[Tuple[int, int], float],
        u_v_own: Dict[Tuple[int, int], float],
        z_th_own: Dict[Tuple[int, int], float],
        u_th_own: Dict[Tuple[int, int], float],
    ) -> AcSubnetResult:
        self._build_model_once()
        model = self._model
        assert model is not None
        rho = float(self.params.rho)
        rho_v = float(self.params.effective_rho_v())
        rho_th = float(self.params.effective_rho_th())
        neigh_v = self._vars["neigh_v"]
        neigh_theta = self._vars["neigh_theta"]
        pij = self._vars["pij"]
        qij = self._vars["qij"]
        v = self._vars["v"]
        theta = self._vars["theta"]

        # Free boundary copies are optimized, with one consensus term per cut.
        cv_copy = {j: 0.0 for j in neigh_v}
        ct_copy = {j: 0.0 for j in neigh_theta}
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            if jj not in neigh_v:
                continue
            zv = float(z_v.get((ii, jj), 1.0))
            zth = float(z_th.get((ii, jj), 0.0))
            # Fallback: letzte Exchange-Werte
            if (ii, jj) not in z_v:
                for rank_payload in neighbor_v.values():
                    if jj in rank_payload:
                        zv = float(rank_payload[jj])
                        break
                for rank_payload in neighbor_theta.values():
                    if jj in rank_payload:
                        zth = float(rank_payload[jj])
                        break
            cv_copy[jj] += self.edge_factor(ii, jj) * (zv - float(u_v.get((ii, jj), 0.0)))
            ct_copy[jj] += self.edge_factor(ii, jj) * (zth - float(u_th.get((ii, jj), 0.0)))
            neigh_v[jj].Start = zv
            neigh_theta[jj].Start = zth
        for j in neigh_v:
            set_consensus_linear(model, neigh_v[j], -rho_v * cv_copy[j])
            set_consensus_linear(model, neigh_theta[j], -rho_th * ct_copy[j])

        # ADMM-Penalty nur auf eigene Cut-Bus-V/θ
        cv_own: Dict[int, float] = {b: 0.0 for b in self.bus_ids}
        cth_own: Dict[int, float] = {b: 0.0 for b in self.bus_ids}
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            cv_own[ii] += self.edge_factor(ii, jj) * float(z_v_own.get((ii, jj), 1.0) - u_v_own.get((ii, jj), 0.0))
            cth_own[ii] += self.edge_factor(ii, jj) * float(z_th_own.get((ii, jj), 0.0) - u_th_own.get((ii, jj), 0.0))
        for bi, csum in cv_own.items():
            if abs(csum) > 0 or bi in self._vars.get("cut_local_buses", set()):
                set_consensus_linear(model, v[bi], -rho_v * csum)
        for bi, csum in cth_own.items():
            bus = self.topology.buses[bi]
            if bus.is_slack:
                continue
            if abs(csum) > 0 or bi in self._vars.get("cut_local_buses", set()):
                set_consensus_linear(model, theta[bi], -rho_th * csum)

        # Warmstart from last accepted subnet solution (helps large partitions)
        if self._last_good is not None:
            prev = self._last_good
            for b in self.bus_ids:
                try:
                    v[b].Start = float(prev.v.get(b, 1.0))
                    theta[b].Start = float(prev.va.get(b, 0.0))
                    self._vars["pg"][b].Start = float(prev.pg.get(b, 0.0))
                    self._vars["qg"][b].Start = float(prev.qg.get(b, 0.0))
                except Exception:
                    pass
            for key, pvar in pij.items():
                if key in (prev.pij or {}):
                    try:
                        pvar.Start = float(prev.pij[key])
                        qij[key].Start = float(prev.qij.get(key, 0.0))
                    except Exception:
                        pass

        def _extract() -> AcSubnetResult:
            v_sol = {b: float(self._vars["v"][b].X) for b in self.bus_ids}
            va_sol = {b: float(self._vars["theta"][b].X) for b in self.bus_ids}
            pg_sol = {b: float(self._vars["pg"][b].X) for b in self.bus_ids}
            qg_sol = {b: float(self._vars["qg"][b].X) for b in self.bus_ids}
            pij_sol = {k: float(var.X) for k, var in pij.items()}
            qij_sol = {k: float(var.X) for k, var in qij.items()}
            v_copy = {jb: float(nvar.X) for jb, nvar in neigh_v.items()}
            va_copy = {jb: float(nvar.X) for jb, nvar in neigh_theta.items()}
            ps = {b: float(self._vars["p_slack"][b].X) for b in self.bus_ids}
            qs = {b: float(self._vars["q_slack"][b].X) for b in self.bus_ids}
            gen_cost = 0.0
            for b in self.bus_ids:
                bus = self.topology.buses[b]
                if bus.is_generator:
                    p = pg_sol[b]
                    gen_cost += bus.cost_quad * p * p + bus.cost_lin * p + bus.cost_const
            line_slacks = [float(s.X) for s in self._vars["s_therm"].values()]
            try:
                obj = float(model.ObjVal)
            except Exception:
                obj = 0.0
            return AcSubnetResult(
                v=v_sol,
                va=va_sol,
                pg=pg_sol,
                qg=qg_sol,
                pij=pij_sol,
                qij=qij_sol,
                objective_cost=obj,
                generation_cost=gen_cost,
                p_slack=ps,
                q_slack=qs,
                line_slack_max=max(line_slacks) if line_slacks else 0.0,
                line_slack_mean=(sum(line_slacks) / len(line_slacks)) if line_slacks else 0.0,
                gen_slack_max=0.0,
                v_copy=v_copy,
                va_copy=va_copy,
            )

        def _acceptable(st: int) -> bool:
            # Strict status acceptance is enforced by _optimize_with_wls_retry;
            # this retains the legacy non-strict incumbent policy.
            sc = int(getattr(model, "SolCount", 0) or 0)
            if sc > 0 and st not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED):
                return True
            return st in (GRB.OPTIMAL, GRB.SUBOPTIMAL)

        # Soft-stagger concurrent NonConvex optimizes (Academic WLS token spikes).
        try:
            stagger_ms = int(float(os.getenv("ADMM_SOLVE_STAGGER_MS", "50") or "50"))
        except ValueError:
            stagger_ms = 50
        if stagger_ms > 0:
            try:
                rank = int(
                    os.getenv("OMPI_COMM_WORLD_RANK")
                    or os.getenv("PMI_RANK")
                    or os.getenv("MPI_LOCALRANKID")
                    or self.agent_id
                    or 0
                )
            except ValueError:
                rank = int(self.agent_id)
            time.sleep(0.001 * stagger_ms * max(0, rank))

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                if not _optimize_with_wls_retry(model, self.agent_id):
                    raise RuntimeError(f"Agent {self.agent_id}: subnet optimizer failed")
                last_err = None
                break
            except Exception as exc:  # GurobiError / WLS token blips
                last_err = exc
                msg = str(exc).lower()
                retryable = any(
                    tok in msg
                    for tok in ("license", "token", "wls", "network", "server")
                )
                print(
                    f"[AC] Agent {self.agent_id}: optimize failed "
                    f"(attempt {attempt + 1}/3): {exc}",
                    flush=True,
                )
                if not retryable or attempt == 2:
                    raise
                time.sleep(1.0 + attempt)
        if last_err is not None:
            raise last_err

        status = int(model.status)
        if not _acceptable(status):
            if os.getenv("ADMM_STRICT", "1") == "1":
                raise RuntimeError(f"Agent {self.agent_id}: local subnet solve failed, status={status}, SolCount={model.SolCount}")
            print(
                f"[AC] Agent {self.agent_id}: subnet status={status} "
                f"SolCount={getattr(model,'SolCount',0)} → rebuild/hold",
                flush=True,
            )
            self.request_rebuild()
            return self._retry_or_hold(
                neighbor_theta, neighbor_v, z_v, u_v, z_th, u_th, z_v_own, u_v_own, z_th_own, u_th_own
            )

        result = _extract()
        self._last_good = result
        return result

    def _retry_or_hold(
        self,
        neighbor_theta,
        neighbor_v,
        z_v,
        u_v,
        z_th,
        u_th,
        z_v_own,
        u_v_own,
        z_th_own,
        u_th_own,
    ) -> AcSubnetResult:
        """Second optimize after rebuild; fall back to last good iterate if needed."""
        # Re-apply objectives/bounds via a shallow second solve body
        self._build_model_once()
        model = self._model
        assert model is not None
        rho = float(self.params.rho)
        neigh_v = self._vars["neigh_v"]
        neigh_theta = self._vars["neigh_theta"]
        v = self._vars["v"]
        theta = self._vars["theta"]
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            if jj not in neigh_v:
                continue
            zv = float(z_v.get((ii, jj), 1.0))
            zth = float(z_th.get((ii, jj), 0.0))
            neigh_v[jj].LB = zv
            neigh_v[jj].UB = zv
            neigh_theta[jj].LB = zth
            neigh_theta[jj].UB = zth
            set_consensus_linear(model, neigh_v[jj], 0.0)
            set_consensus_linear(model, neigh_theta[jj], 0.0)
        cv_own: Dict[int, float] = {b: 0.0 for b in self.bus_ids}
        cth_own: Dict[int, float] = {b: 0.0 for b in self.bus_ids}
        for (i, j) in self.cut_edge_lines:
            ii, jj = (i, j) if i in self.bus_id_set else (j, i)
            cv_own[ii] += self.edge_factor(ii, jj) * float(z_v_own.get((ii, jj), 1.0) - u_v_own.get((ii, jj), 0.0))
            cth_own[ii] += self.edge_factor(ii, jj) * float(z_th_own.get((ii, jj), 0.0) - u_th_own.get((ii, jj), 0.0))
        for bi, csum in cv_own.items():
            if abs(csum) > 0 or bi in self._vars.get("cut_local_buses", set()):
                set_consensus_linear(model, v[bi], -rho * csum)
        for bi, csum in cth_own.items():
            if self.topology.buses[bi].is_slack:
                continue
            if abs(csum) > 0 or bi in self._vars.get("cut_local_buses", set()):
                set_consensus_linear(model, theta[bi], -rho * csum)
        if self._last_good is not None:
            prev = self._last_good
            for b in self.bus_ids:
                try:
                    v[b].Start = float(prev.v.get(b, 1.0))
                    theta[b].Start = float(prev.va.get(b, 0.0))
                except Exception:
                    pass
        # Retry with more time once
        try:
            cur_tl = float(model.Params.TimeLimit)
            if cur_tl > 0:
                model.setParam(GRB.Param.TimeLimit, max(cur_tl, cur_tl * 2.0))
        except Exception:
            pass
        model.optimize()
        # restore TL
        try:
            tl_env = os.getenv("GRB_TIMELIMIT")
            if tl_env not in (None, ""):
                model.setParam(GRB.Param.TimeLimit, float(tl_env))
        except Exception:
            pass
        status = int(model.status)
        sc = int(getattr(model, "SolCount", 0) or 0)
        if sc > 0 and status not in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED):
            pij = self._vars["pij"]
            qij = self._vars["qij"]
            v_sol = {b: float(self._vars["v"][b].X) for b in self.bus_ids}
            va_sol = {b: float(self._vars["theta"][b].X) for b in self.bus_ids}
            pg_sol = {b: float(self._vars["pg"][b].X) for b in self.bus_ids}
            qg_sol = {b: float(self._vars["qg"][b].X) for b in self.bus_ids}
            pij_sol = {k: float(var.X) for k, var in pij.items()}
            qij_sol = {k: float(var.X) for k, var in qij.items()}
            v_copy = {jb: float(nvar.X) for jb, nvar in neigh_v.items()}
            va_copy = {jb: float(nvar.X) for jb, nvar in neigh_theta.items()}
            ps = {b: float(self._vars["p_slack"][b].X) for b in self.bus_ids}
            qs = {b: float(self._vars["q_slack"][b].X) for b in self.bus_ids}
            gen_cost = 0.0
            for b in self.bus_ids:
                bus = self.topology.buses[b]
                if bus.is_generator:
                    p = pg_sol[b]
                    gen_cost += bus.cost_quad * p * p + bus.cost_lin * p + bus.cost_const
            line_slacks = [float(s.X) for s in self._vars["s_therm"].values()]
            result = AcSubnetResult(
                v=v_sol,
                va=va_sol,
                pg=pg_sol,
                qg=qg_sol,
                pij=pij_sol,
                qij=qij_sol,
                objective_cost=float(getattr(model, "ObjVal", 0.0) or 0.0),
                generation_cost=gen_cost,
                p_slack=ps,
                q_slack=qs,
                line_slack_max=max(line_slacks) if line_slacks else 0.0,
                line_slack_mean=(sum(line_slacks) / len(line_slacks)) if line_slacks else 0.0,
                gen_slack_max=0.0,
                v_copy=v_copy,
                va_copy=va_copy,
            )
            self._last_good = result
            return result
        if self._last_good is not None:
            print(
                f"[AC] Agent {self.agent_id}: subnet fail status={status} → hold last-good iterate",
                flush=True,
            )
            return self._last_good
        raise RuntimeError(f"Agent {self.agent_id}: AC subnet solve failed, status={status}")
