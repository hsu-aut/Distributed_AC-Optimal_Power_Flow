import copy
import math
import cmath
import unittest
from pathlib import Path
from unittest.mock import patch

from distributed_opf.config import load_topology_config
from distributed_opf.ac_validation import validate_ac
from distributed_opf.admm_ac import AcOpfSubnetProblem
from distributed_opf.admm_params import AdmmParams
from distributed_opf.ac_feasibility import balance_from_agent_states
from distributed_opf.admm_ac import AcLocalState

ROOT = Path(__file__).resolve().parents[1]


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.topo = load_topology_config(ROOT / "topology_3bus.yaml")
        self.zero = {b: {"v": 1., "va": 0., "pg": 0., "qg": 0.} for b in self.topo.buses}

    def test_missing_and_nonfinite_rejected(self):
        for states in ({}, {0: self.zero[0]}, {**self.zero, 1: {**self.zero[1], "qg": math.nan}}):
            self.assertFalse(validate_ac(self.topo, states)["valid_data"])

    def test_only_reverse_end_overload_is_detected(self):
        line = self.topo.lines[0]
        line.b_shunt_pu = 0.0
        states = copy.deepcopy(self.zero)
        i, j = line.from_bus, line.to_bus
        states[i].update(v=1., va=0.)
        states[j].update(v=1.06, va=-0.1)
        current = abs((1.-1.06*cmath.exp(-0.1j))/complex(line.r_pu, line.x_pu))
        line.smax_pu = 1.03*current
        result = validate_ac(self.topo, states)
        forward, reverse = result["branches"][:2]
        self.assertAlmostEqual(forward["s"], current)
        self.assertAlmostEqual(reverse["s"], 1.06*current)
        self.assertEqual(forward["violation"], 0.)
        self.assertGreater(reverse["violation"], 0.)

    def test_balanced_zero_load_candidate_passes(self):
        for bus in self.topo.buses.values():
            bus.demand_p = bus.demand_q = 0.
        self.assertTrue(validate_ac(self.topo, self.zero)["feasible"])

    def test_reverse_thermal_end_and_reactive_limit_checked(self):
        self.topo.lines[0].b_shunt_pu = 2.
        self.topo.lines[0].smax_pu = .95
        self.zero[0]["v"] = .9
        self.zero[1]["v"] = 1.1
        result = validate_ac(self.topo, self.zero)
        self.assertEqual(len(result["branches"]), 2*len(self.topo.lines))
        reverse = next(b for b in result["branches"] if b["branch_id"] == 0 and b["from"] == 1)
        self.assertGreater(reverse["violation"], 0.)
        self.zero[0]["qg"] = 2.
        self.assertGreater(validate_ac(self.topo, self.zero)["max_qg_violation"], 1.)

    def test_parallel_branches_preserved_in_subnets(self):
        topo = load_topology_config(ROOT / "topology_ieee118_full.yaml")
        with patch.object(AcOpfSubnetProblem, "_build_model_once", lambda self: None):
            whole = AcOpfSubnetProblem(0, list(topo.buses), topo, AdmmParams(), {})
            self.assertEqual(len(whole.internal_lines), 186)
            local = [41]
            cuts = {1: [(41,j) for j in topo.buses[41].neighbors]}
            one = AcOpfSubnetProblem(0, local, topo, AdmmParams(), cuts)
            self.assertEqual(sum(j == 48 for _,i,j in one.boundary_lines), 2)

    def test_dataclass_state_balance(self):
        states = {b: AcLocalState() for b in self.topo.buses}
        result = balance_from_agent_states(self.topo, states)
        self.assertGreater(result["max_dP"], .7)


if __name__ == "__main__":
    unittest.main()
