import unittest
from types import SimpleNamespace

from distributed_opf.admm_ac import AcLocalState, admm_u_step_ac, admm_z_step_ac
from distributed_opf.admm_params import AdmmParams
from distributed_opf.agent_ac_mpi import AcAgent, balanced_rho


class AdmmVariantTests(unittest.TestCase):
    def _pair(self):
        local = AcLocalState(v=1., va=0., v_copy={1: 1.}, va_copy={1: 3.},
                             uv={1: 0.}, uth={1: .2}, uv_own={1: 0.}, uth_own={1: 0.},
                             zv={1: 1.}, zth={1: 1.}, zv_own={1: 1.}, zth_own={1: 0.})
        other = AcLocalState(v=1., va=-3., v_copy={0: 1.}, va_copy={0: 0.},
                             uv={0: 0.}, uth={0: 0.}, uv_own={0: 0.}, uth_own={0: -.1})
        return local, other, SimpleNamespace(bus_id=0, neighbors=[1])

    def test_relaxed_dual_update_uses_relaxed_iterate(self):
        local, other, problem = self._pair()
        params = AdmmParams(rho=100., relaxation=1.5)
        zv, zt, zvo, zto, _ = admm_z_step_ac(problem, local, {1: other}, params)
        # x_hat = 1.5*3 - 0.5*1 = 4 for the copy, 1.5*(-3) - 0.5*1 = -5 for the neighbor's own angle.
        self.assertAlmostEqual(zt[1], .5*((4. + .2) + (-5. - .1)))
        updated = admm_u_step_ac(problem, local, zv, zt, zvo, zto, params)
        self.assertAlmostEqual(updated.uth[1], .2 + (4. - zt[1]))
        # The primal residual remains x - z, independent of relaxation.
        self.assertAlmostEqual(updated.prim_th, ((3. - zt[1])**2 + (0. - zto[1])**2) ** .5)

    def test_unrelaxed_update_is_unchanged(self):
        local, other, problem = self._pair()
        params = AdmmParams(rho=100., relaxation=1.)
        zv, zt, zvo, zto, _ = admm_z_step_ac(problem, local, {1: other}, params)
        updated = admm_u_step_ac(problem, local, zv, zt, zvo, zto, params)
        self.assertEqual(updated.uth[1], .2 + (3. - zt[1]))

    def test_balanced_rho_rule(self):
        self.assertEqual(balanced_rho(1e7, 50., 2., 10., 2., 1e6, 1e8), 2e7)
        self.assertEqual(balanced_rho(1e7, 2., 50., 10., 2., 1e6, 1e8), 5e6)
        self.assertEqual(balanced_rho(1e7, 5., 2., 10., 2., 1e6, 1e8), 1e7)
        self.assertEqual(balanced_rho(1e8, 50., 2., 10., 2., 1e6, 1e8), 1e8)
        self.assertEqual(balanced_rho(1e6, 2., 50., 10., 2., 1e6, 1e8), 1e6)

    def test_rho_change_converts_scaled_duals(self):
        agent = AcAgent.__new__(AcAgent)
        agent.params = AdmmParams(rho=1e7, rho_v=1e7, rho_th=1e7)
        agent._single_bus_mode = True
        agent.state = AcLocalState(uv={1: 3.}, uth={1: -4.}, uv_own={1: 1.}, uth_own={1: 2.})
        agent.states = {0: agent.state}
        agent.rank, agent.iteration, agent.rho_updates_total, agent.iters_since_rho_update = 1, 100, 0, 50
        agent._adaptive_rho_until, agent._rho_history = 0, []
        agent._adaptive_rho_mu, agent._adaptive_rho_tau = 10., 2.
        agent._adaptive_rho_min, agent._adaptive_rho_max = 1e6, 1e8
        agent._last_global_ratio_primal, agent._last_global_ratio_dual = 1., 400.
        agent._request_model_rebuild = lambda: None
        unscaled_before = agent.params.rho * agent.states[0].uth[1]
        agent._normalized_rho_update()
        self.assertEqual(agent.params.rho, 5e6)
        self.assertEqual(agent.params.effective_rho_th(), 5e6)
        # The unscaled multiplier lambda = rho*u is preserved.
        self.assertEqual(agent.params.rho * agent.states[0].uth[1], unscaled_before)
        self.assertEqual(agent._rho_history[0]["rho_new"], 5e6)


if __name__ == "__main__":
    unittest.main()
