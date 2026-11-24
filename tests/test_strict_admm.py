import unittest
from types import SimpleNamespace
from distributed_opf.agent_ac_mpi import AcAgent
from distributed_opf.admm_ac import AcLocalState, admm_z_step_ac, admm_u_step_ac, _strict_solve_accepted
from distributed_opf.admm_params import AdmmParams


class StrictAdmmTests(unittest.TestCase):
    def test_strict_subproblem_rejects_unfinished_incumbents(self):
        self.assertTrue(_strict_solve_accepted(2, 1))
        self.assertTrue(_strict_solve_accepted(18, 1))
        for status, count in ((9, 1), (13, 1), (3, 0), (18, 0)):
            self.assertFalse(_strict_solve_accepted(status, count))

    def test_angle_consensus_is_euclidean_quadratic_minimizer(self):
        local = AcLocalState(v=1.,va=0.,v_copy={1:1.},va_copy={1:3.},
                             uv={1:0.},uth={1:.2},uv_own={1:0.},uth_own={1:0.})
        other = AcLocalState(v=1.,va=-3.,v_copy={0:1.},va_copy={0:0.},
                             uv={0:0.},uth={0:0.},uv_own={0:0.},uth_own={0:-.1})
        problem = SimpleNamespace(bus_id=0,neighbors=[1])
        params = AdmmParams(rho=100.,relaxation=1.)
        zv,zt,zvo,zto,dual = admm_z_step_ac(problem,local,{1:other},params)
        # Derivative of (z-3-.2)^2+(z+3+.1)^2 vanishes at z=.05.
        self.assertAlmostEqual(zt[1],.05)
        updated = admm_u_step_ac(problem,local,zv,zt,zvo,zto,params)
        self.assertAlmostEqual(updated.uth[1],3.15)
        self.assertGreater(updated.prim_res,2.9)

    def test_large_rho_does_not_bypass_dual_tolerance(self):
        agent = AcAgent.__new__(AcAgent)
        agent.params = AdmmParams(rho=10000., abs_tol=1e-5, rel_tol=1e-5)
        agent.problem = SimpleNamespace(neighbors=[1])
        agent._single_bus_mode = True
        agent.state = AcLocalState(v=1., va=0., prim_res=0., dual_res=1e-3)
        agent.strict = True
        self.assertFalse(agent._check_convergence_local())
        agent.state.dual_res = 1e-6
        self.assertTrue(agent._check_convergence_local())


if __name__ == "__main__":
    unittest.main()
