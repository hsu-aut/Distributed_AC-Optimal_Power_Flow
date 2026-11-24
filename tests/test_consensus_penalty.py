import os
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.getenv("RUN_GUROBI_TESTS") == "1", "requires a Gurobi license")
class CenteredPenaltyIntegrationTests(unittest.TestCase):
    def test_shared_occurrences_and_changed_targets_preserve_objective(self):
        import gurobipy as gp
        from distributed_opf.consensus_penalty import install_centered_penalties, set_consensus_linear
        for rho in (100., 1e8):
            with patch.dict(os.environ, {"ADMM_CENTERED_PENALTY": "1"}), gp.Model() as model:
                model.Params.OutputFlag = 0
                voltage = model.addVar(lb=1.03, ub=1.03)
                generation = model.addVar(lb=.4, ub=.4)
                model.setObjective(2*generation*generation + 3*generation + 1.5*rho*voltage*voltage)
                model.update()
                install_centered_penalties(model, [(voltage, rho)]*3)
                for targets in ((.95, 1.01, .98), (1.04, .99, 1.02)):
                    set_consensus_linear(model, voltage, -rho*sum(targets))
                    model.optimize()
                    self.assertEqual(model.Status, gp.GRB.OPTIMAL)
                    mean = sum(targets)/3
                    omitted_scatter = .5*rho*sum((t-mean)**2 for t in targets)
                    direct = 2*.4**2 + 3*.4 + .5*rho*sum((1.03-t)**2 for t in targets)
                    self.assertAlmostEqual(model.ObjVal + omitted_scatter, direct, delta=1e-6)
