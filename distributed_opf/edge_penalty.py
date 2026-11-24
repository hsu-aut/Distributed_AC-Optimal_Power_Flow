"""Edge-weighted consensus penalties: a separate ADMM variant, not standard ADMM.

Each consensus edge (bus pair i-j) gets rho_e = rho * f_e for both its V and its
theta consensus groups, with

    f_e = clamp((|y_e| / median |y|) ** exponent, min_factor, max_factor),

|y_e| the summed series admittance of all parallel branches between i and j.
Both copies in a consensus group share rho_e, so the z-step stays the plain mean;
the x-step penalty, the dual residual rho_e * dz and the dual tolerance use rho_e.
This is ADMM with a fixed diagonal penalty matrix. exponent = 0 gives f_e = 1.0
exactly and must reproduce standard ADMM bit for bit.
"""
from __future__ import annotations

import math
import os
import statistics

from distributed_opf.partitioning import pair_weights

MODES = ("none", "admittance")


class EdgePenalty:
    def __init__(self, topology, exponent: float = 1.0, min_factor: float = 0.0, max_factor: float = math.inf):
        weights = pair_weights(topology)
        if not weights:
            raise ValueError("Edge-weighted penalties need at least one branch")
        self.reference = statistics.median(weights.values())
        self.exponent = float(exponent)
        self.min_factor = float(min_factor)
        self.max_factor = float(max_factor)
        if self.max_factor < self.min_factor:
            raise ValueError("max_factor must not be below min_factor")
        self.factors = {pair: min(self.max_factor, max(self.min_factor, (w / self.reference) ** self.exponent))
                        for pair, w in weights.items()}
        if any(not (f > 0 and math.isfinite(f)) for f in self.factors.values()):
            raise ValueError("Edge penalty factors must be positive and finite")

    @classmethod
    def from_env(cls, topology):
        mode = os.getenv("ADMM_EDGE_RHO", "none").strip().lower()
        if mode == "none":
            return None
        if mode != "admittance":
            raise ValueError(f"Unknown ADMM_EDGE_RHO mode {mode!r}; expected one of {MODES}")
        max_factor = float(os.getenv("ADMM_EDGE_RHO_MAX_FACTOR", "0") or 0)
        return cls(topology,
                   exponent=float(os.getenv("ADMM_EDGE_RHO_EXPONENT", "1") or 1),
                   min_factor=float(os.getenv("ADMM_EDGE_RHO_MIN_FACTOR", "0") or 0),
                   max_factor=max_factor if max_factor > 0 else math.inf)

    def __call__(self, i: int, j: int) -> float:
        return self.factors[(i, j) if i < j else (j, i)]

    def config(self):
        return {"mode": "admittance", "exponent": self.exponent, "min_factor": self.min_factor,
                "max_factor": None if math.isinf(self.max_factor) else self.max_factor,
                "reference_admittance": self.reference}

    def summary(self):
        values = sorted(self.factors.values())
        return {**self.config(), "edges": len(values), "factor_min": values[0],
                "factor_median": statistics.median(values), "factor_max": values[-1]}
