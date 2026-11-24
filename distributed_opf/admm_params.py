"""ADMM parameters shared by the central reference and the distributed AC-OPF."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AdmmParams:
    rho: float = 10000.0
    rho_v: float | None = None  # Voltage-Konsens; default = rho
    rho_th: float | None = None  # Winkel-Konsens; default = rho
    max_iters: int = 200
    rel_tol: float = 1e-4
    abs_tol: float = 1e-4
    use_slack: bool = True   # p_slack aktiviert
    p_slack_weight: float = 1e5  # p_slack (Leistungsbilanz-Slack am Slack-Bus)
    line_slack_weight: float = 1e5  # Leitungen
    gen_slack_weight: float = 1e6  # Generatoren (deutlich größer)
    relaxation: float = 1.0  # Over-Relaxation Parameter α (1.0 = aus)
    phys_tol: float = 1e-3

    def effective_rho_v(self) -> float:
        return float(self.rho if self.rho_v is None else self.rho_v)

    def effective_rho_th(self) -> float:
        return float(self.rho if self.rho_th is None else self.rho_th)

    @staticmethod
    def _parse_bool(val: str, default: bool) -> bool:
        if val is None:
            return default
        v = val.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        return default

    @classmethod
    def from_env(cls) -> "AdmmParams":
        """Build parameters from ADMM_* environment variables; unset or invalid values keep defaults.

        ADMM_RHO, ADMM_RHO_V, ADMM_RHO_THETA, ADMM_MAX_ITERS, ADMM_REL_TOL, ADMM_ABS_TOL,
        ADMM_USE_SLACK, ADMM_P_SLACK_WEIGHT, ADMM_LINE_SLACK_WEIGHT, ADMM_GEN_SLACK_WEIGHT,
        ADMM_RELAXATION, ADMM_PHYS_TOL.
        """
        p = cls()

        def _get_float(name: str, default: float) -> float:
            try:
                s = os.getenv(name, None)
                return float(s) if s is not None and s.strip() != "" else default
            except Exception:
                return default

        def _get_int(name: str, default: int) -> int:
            try:
                s = os.getenv(name, None)
                return int(float(s)) if s is not None and s.strip() != "" else default
            except Exception:
                return default

        p.rho = _get_float("ADMM_RHO", p.rho)
        # Optional separate penalties; empty/unset -> None -> fall back to rho
        try:
            s = os.getenv("ADMM_RHO_V", None)
            p.rho_v = float(s) if s is not None and s.strip() != "" else None
        except Exception:
            p.rho_v = None
        try:
            s = os.getenv("ADMM_RHO_THETA", None)
            p.rho_th = float(s) if s is not None and s.strip() != "" else None
        except Exception:
            p.rho_th = None
        p.max_iters = _get_int("ADMM_MAX_ITERS", p.max_iters)
        p.rel_tol = _get_float("ADMM_REL_TOL", p.rel_tol)
        p.abs_tol = _get_float("ADMM_ABS_TOL", p.abs_tol)
        p.use_slack = cls._parse_bool(os.getenv("ADMM_USE_SLACK"), p.use_slack)
        p.p_slack_weight = _get_float("ADMM_P_SLACK_WEIGHT", p.p_slack_weight)
        p.line_slack_weight = _get_float("ADMM_LINE_SLACK_WEIGHT", p.line_slack_weight)
        p.gen_slack_weight = _get_float("ADMM_GEN_SLACK_WEIGHT", p.gen_slack_weight)
        p.relaxation = _get_float("ADMM_RELAXATION", p.relaxation)
        p.phys_tol = _get_float("ADMM_PHYS_TOL", p.phys_tol)
        return p
