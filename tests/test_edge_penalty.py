import math
import os
import statistics
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from distributed_opf.admm_ac import AcLocalState, admm_z_step_ac
from distributed_opf.admm_params import AdmmParams
from distributed_opf.agent_ac_mpi import AcAgent
from distributed_opf.config import load_topology_config
from distributed_opf.edge_penalty import EdgePenalty

ROOT = Path(__file__).resolve().parents[1]


class EdgePenaltyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topology = load_topology_config(ROOT / "topology_ieee118_full.yaml")

    def test_symmetric_and_normalized_to_median(self):
        edge = EdgePenalty(self.topology)
        self.assertEqual(edge(67, 115), edge(115, 67))
        self.assertAlmostEqual(statistics.median(edge.factors.values()), 1.0)
        self.assertGreater(edge(67, 115), 5.0)

    def test_exponent_zero_is_exactly_standard_admm(self):
        edge = EdgePenalty(self.topology, exponent=0.0)
        self.assertTrue(all(f == 1.0 for f in edge.factors.values()))

    def test_clamp_and_environment(self):
        edge = EdgePenalty(self.topology, exponent=1.0, min_factor=0.5, max_factor=2.0)
        self.assertTrue(all(0.5 <= f <= 2.0 for f in edge.factors.values()))
        with mock.patch.dict(os.environ, {"ADMM_EDGE_RHO": "none"}):
            self.assertIsNone(EdgePenalty.from_env(self.topology))
        with mock.patch.dict(os.environ, {"ADMM_EDGE_RHO": "admittance", "ADMM_EDGE_RHO_EXPONENT": "0.5",
                                          "ADMM_EDGE_RHO_MAX_FACTOR": "0"}):
            edge = EdgePenalty.from_env(self.topology)
            self.assertEqual(edge.exponent, 0.5)
            self.assertTrue(math.isinf(edge.max_factor))
        with mock.patch.dict(os.environ, {"ADMM_EDGE_RHO": "bogus"}):
            with self.assertRaises(ValueError):
                EdgePenalty.from_env(self.topology)

    def test_dual_residual_and_tolerance_use_edge_rho(self):
        local = AcLocalState(v=1., va=0., v_copy={1: 1.}, va_copy={1: 0.2}, uv={1: 0.}, uth={1: .3},
                             uv_own={1: 0.}, uth_own={1: 0.}, zv={1: 1.}, zth={1: 0.}, zv_own={1: 1.}, zth_own={1: 0.})
        other = AcLocalState(v=1., va=0., v_copy={0: 1.}, va_copy={0: 0.}, uv={0: 0.}, uth={0: 0.},
                             uv_own={0: 0.}, uth_own={0: 0.})
        params = AdmmParams(rho=100., relaxation=1.)
        plain = SimpleNamespace(bus_id=0, neighbors=[1], edge=None)
        weighted = SimpleNamespace(bus_id=0, neighbors=[1], edge=lambda i, j: 3.0)
        dual_plain = admm_z_step_ac(plain, local, {1: other}, params)[4]
        dual_weighted = admm_z_step_ac(weighted, local, {1: other}, params)[4]
        self.assertAlmostEqual(dual_weighted, 3.0 * dual_plain)

        agent = AcAgent.__new__(AcAgent)
        agent.params = AdmmParams(rho=100., abs_tol=1e-5, rel_tol=1e-5)
        agent.problem = SimpleNamespace(neighbors=[1])
        agent._single_bus_mode, agent.strict, agent.bus_id = True, True, 0
        agent.state = AcLocalState(v=1., va=0., uth={1: 1.}, prim_res=0., dual_res=0.)
        agent.edge_penalty = None
        agent._check_convergence_local()
        base = agent._last_local_ratios
        agent.state.dual_res = 1e-3
        agent._check_convergence_local()
        ratio_plain = agent._last_local_ratios[1]
        agent.edge_penalty = lambda i, j: 3.0
        agent._check_convergence_local()
        self.assertLess(agent._last_local_ratios[1], ratio_plain)
        self.assertEqual(base[1], 0.0)


if __name__ == "__main__":
    unittest.main()
