import math
import unittest
from types import SimpleNamespace

from distributed_opf.admm_ac import AcLocalState
from distributed_opf.admm_params import AdmmParams
from distributed_opf.agent_ac_mpi import AcAgent, global_boyd_ratios


class ResidualTraceTests(unittest.TestCase):
    def test_global_boyd_ratios(self):
        # m=16, ||r||=3e-5, ||s||=4, ||x||=5, ||z||=2, ||rho u||=1e3
        result = global_boyd_ratios((16., 9e-10, 16., 25., 4., 1e6), abs_tol=1e-5, rel_tol=1e-5)
        self.assertAlmostEqual(result["eps_pri"], 4e-5 + 5e-5)
        self.assertAlmostEqual(result["eps_dual"], 4e-5 + 1e-2)
        self.assertAlmostEqual(result["primal_ratio"], 3e-5 / 9e-5)
        self.assertAlmostEqual(result["dual_ratio"], 4. / (4e-5 + 1e-2))

    def test_partition_copies_counted_once(self):
        agent = AcAgent.__new__(AcAgent)
        agent.params = AdmmParams(rho=1., abs_tol=1e-5, rel_tol=1e-5)
        agent._single_bus_mode, agent.strict, agent.bus_id = False, True, 0
        agent.bus_ids = [0, 1, 2]
        agent.cut_edge_lines = [(0, 7)]
        agent.z_v = agent.z_th = agent.z_v_own = agent.z_th_own = {(0, 7): 0.}
        agent.u_v = agent.u_th = agent.u_v_own = agent.u_th_own = {(0, 7): 0.}
        copies = {7: 3.}
        agent.states = {b: AcLocalState(v=0., va=0., v_copy=copies, va_copy={7: 4.}) for b in agent.bus_ids}
        agent.state = agent.states[0]
        agent.edge_penalty = None
        agent._check_convergence_local()
        # ||x||² = 3² + 4² once, not three times.
        self.assertEqual(agent._last_local_norms[3], 25.)


if __name__ == "__main__":
    unittest.main()
