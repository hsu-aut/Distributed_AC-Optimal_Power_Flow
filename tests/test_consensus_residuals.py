import math
import unittest

from distributed_opf.consensus_residuals import consensus_ratios, consensus_terms


class ConsensusResidualTests(unittest.TestCase):
    # One own variable in two groups (rho 2 and 3), one copy in one group (rho 2).
    MEMBERS = [
        ("own", 1.0, 0.9, 1.0, 0.1, 2.0),
        ("own", 1.0, 0.8, 0.7, 0.2, 3.0),
        ("copy", 0.5, 0.6, 0.5, -0.1, 2.0),
    ]

    def test_terms_aggregate_dual_quantities_per_variable(self):
        p, n, r2, s2, ax2, ez2, y2 = consensus_terms(self.MEMBERS)
        self.assertEqual((p, n), (3.0, 2.0))
        self.assertAlmostEqual(r2, 0.01 + 0.04 + 0.01)
        self.assertAlmostEqual(ax2, 1.0 + 1.0 + 0.25)
        self.assertAlmostEqual(ez2, 0.81 + 0.64 + 0.36)
        # s_own = 2*(0.9-1.0) + 3*(0.8-0.7) = 0.1, s_copy = 2*0.1 = 0.2 (A^T aggregation);
        # summing squares per member instead would give 0.04 + 0.09 + 0.04 = 0.17.
        self.assertAlmostEqual(s2, 0.1 ** 2 + 0.2 ** 2)
        # (A^T R u)_own = 2*0.1 + 3*0.2 = 0.8, (A^T R u)_copy = -0.2
        self.assertAlmostEqual(y2, 0.8 ** 2 + 0.2 ** 2)

    def test_ratios_follow_boyd_tolerances(self):
        terms = consensus_terms(self.MEMBERS)
        result = consensus_ratios(terms, abs_tol=1e-3, rel_tol=1e-2)
        eps_pri = math.sqrt(3) * 1e-3 + 1e-2 * max(math.sqrt(2.25), math.sqrt(1.81))
        eps_dual = math.sqrt(2) * 1e-3 + 1e-2 * math.sqrt(0.68)
        self.assertAlmostEqual(result["eps_pri"], eps_pri)
        self.assertAlmostEqual(result["eps_dual"], eps_dual)
        self.assertAlmostEqual(result["primal_ratio"], math.sqrt(0.06) / eps_pri)
        self.assertAlmostEqual(result["dual_ratio"], math.sqrt(0.05) / eps_dual)

    def test_rank_sums_equal_global_terms(self):
        # Variables are rank-local, so summing per-rank terms reproduces the global terms exactly.
        rank_a = consensus_terms(self.MEMBERS[:2])
        rank_b = consensus_terms(self.MEMBERS[2:])
        total = consensus_terms(self.MEMBERS)
        for a, b, t in zip(rank_a, rank_b, total):
            self.assertAlmostEqual(a + b, t)

    def test_rank_without_consensus_constraints(self):
        self.assertEqual(consensus_ratios(consensus_terms([]), 1e-5, 1e-5)["dual_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
