"""Stopping quantities of the consensus ADMM in the matrix form of Boyd et al. (2011), eq. 3.11-3.12.

Coupling structure used by the AC agents. For every cut edge e = {i, j} and every endpoint bus
b in {i, j} there are two consensus groups g = (e, b, V) and (e, b, theta), each with one global
value z_g and two members:

    own member:  the owner rank's own variable V_b (or theta_b)
    copy member: the other rank's local copy of V_b (or theta_b)

The constraint of member m is x_var(m) - z_g(m) = 0, i.e. A x + B z = 0 with A selecting the
variable of each member and B = -E, E selecting the group of each member. One own variable can
belong to several groups (a bus with several cut edges) and one copy can belong to several groups
(several local buses adjacent to the same foreign bus). With diagonal penalties R (rho_g per group,
edge-weighted variant included) and scaled duals u per member:

    r = A x - E z                      ||r||^2 = sum_m (x_var(m) - z_g(m))^2
    s = A^T R E (z - z_prev)           s_var   = sum_{m of var} rho_g(m) (z_g(m) - z_prev_g(m))
    eps_pri  = sqrt(p) eps_abs + eps_rel max(||A x||, ||E z||)      p = number of members
    eps_dual = sqrt(n) eps_abs + eps_rel ||A^T R u||                n = number of coupled variables

||A x||^2 counts a variable once per member, ||E z||^2 a group value once per member. The
per-rank terms add up exactly to the global terms because every member and every coupled
variable belongs to exactly one rank. These quantities are recorded for reporting; the solver's
stopping decision is documented separately (docs/FORMULATION.md, section 4).
"""
from __future__ import annotations

import math
from typing import Dict, Hashable, Iterable, Tuple

# (variable key, x value, new z, previous z, new scaled dual u, rho of the group)
Member = Tuple[Hashable, float, float, float, float, float]

TERM_FIELDS = ("members", "variables", "r2", "s2", "ax2", "ez2", "y2")


def consensus_terms(members: Iterable[Member]) -> Tuple[float, ...]:
    """Rank-local sums (p, n, ||r||^2, ||s||^2, ||Ax||^2, ||Ez||^2, ||A^T R u||^2)."""
    count = r2 = ax2 = ez2 = 0.0
    s_var: Dict[Hashable, float] = {}
    y_var: Dict[Hashable, float] = {}
    for key, x, z, z_prev, u, rho in members:
        count += 1.0
        r2 += (x - z) ** 2
        ax2 += x * x
        ez2 += z * z
        s_var[key] = s_var.get(key, 0.0) + rho * (z - z_prev)
        y_var[key] = y_var.get(key, 0.0) + rho * u
    return (count, float(len(s_var)), r2, sum(v * v for v in s_var.values()), ax2, ez2,
            sum(v * v for v in y_var.values()))


def consensus_ratios(terms, abs_tol: float, rel_tol: float) -> Dict[str, float]:
    """Boyd stopping quotients from (rank-local or rank-summed) terms; <= 1 means satisfied."""
    p, n, r2, s2, ax2, ez2, y2 = (float(v) for v in terms)
    if p == 0:  # a rank without cut edges has no consensus constraints
        return {"r": 0.0, "s": 0.0, "eps_pri": 0.0, "eps_dual": 0.0, "members": 0.0, "variables": 0.0,
                "primal_ratio": 0.0, "dual_ratio": 0.0}
    r, s = math.sqrt(r2), math.sqrt(s2)
    eps_pri = math.sqrt(p) * abs_tol + rel_tol * max(math.sqrt(ax2), math.sqrt(ez2))
    eps_dual = math.sqrt(n) * abs_tol + rel_tol * math.sqrt(y2)
    return {"r": r, "s": s, "eps_pri": eps_pri, "eps_dual": eps_dual, "members": p, "variables": n,
            "primal_ratio": r / eps_pri if eps_pri > 0 else math.inf,
            "dual_ratio": s / eps_dual if eps_dual > 0 else math.inf}
