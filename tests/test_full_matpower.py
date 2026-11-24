import cmath
from dataclasses import replace
from pathlib import Path
import unittest
import numpy as np
from pandapower.pypower.makeYbus import makeYbus
from distributed_opf.config import load_topology_config, terminal_admittance
from distributed_opf.matpower_case import internal_case
from distributed_opf.ac_validation import validate_ac
from distributed_opf.pwl_ac import AcPwlParams

ROOT = Path(__file__).resolve().parents[1]


class FullMatpowerTests(unittest.TestCase):
    def setUp(self):
        self.case = internal_case(ROOT / "data/matpower/case118.m")
        self.topo = load_topology_config(ROOT / "topology_ieee118_full.yaml")

    def test_original_counts_limits_cost_scaling_and_reference_voltage(self):
        self.assertEqual((len(self.topo.buses), len(self.topo.lines)), (118, 186))
        self.assertEqual(sum(b.is_generator for b in self.topo.buses.values()), 54)
        for gen, cost in zip(self.case['gen'], self.case['gencost']):
            b = self.topo.buses[int(gen[0])]
            np.testing.assert_allclose([b.pg_min*100, b.pg_max*100, b.qg_min*100, b.qg_max*100], gen[[9,8,4,3]])
            self.assertAlmostEqual(b.cost_quad*1.23**2+b.cost_lin*1.23+b.cost_const, np.polyval(cost[4:],123))
        self.assertFalse(self.topo.buses[68].fix_slack_voltage)
        self.assertTrue(all(line.unlimited for line in self.topo.lines))

    def test_full_case_forces_exact_equations_and_hard_limits(self):
        params = AcPwlParams(native_trig=False,exact_bilinear=False,soft_generation=True,soft_thermal=True)
        params.configure_topology(self.topo)
        self.assertTrue(params.full_ac_model and params.native_trig and params.exact_bilinear)
        self.assertFalse(params.soft_generation or params.soft_thermal or params.balance_slack_all_buses)

    def check_admittances_and_validation(self, case, topo):
        y, yf, yt = makeYbus(case['baseMVA'], case['bus'], case['branch'])
        ours = np.diag([complex(b.shunt_g_pu,b.shunt_b_pu) for b in topo.buses.values()])
        for line in topo.lines:
            for i,j in ((line.from_bus,line.to_bus),(line.to_bus,line.from_bus)):
                ys, ym = terminal_admittance(line,i)
                ours[i,i] += ys
                ours[i,j] += ym
        np.testing.assert_allclose(ours, y.toarray(), rtol=0, atol=1e-12)
        rng = np.random.default_rng(739)
        voltage = rng.uniform(.95,1.05,len(topo.buses))*np.exp(1j*rng.uniform(-.4,.4,len(topo.buses)))
        inj = voltage*np.conj(y@voltage)
        states = {i: dict(v=abs(v),va=cmath.phase(v),pg=topo.buses[i].demand_p+inj[i].real,
                         qg=topo.buses[i].demand_q+inj[i].imag) for i,v in enumerate(voltage)}
        result = validate_ac(topo,states)
        self.assertLess(result['max_dP'],1e-11)
        self.assertLess(result['max_dQ'],1e-11)
        sf = voltage[case['branch'][:,0].astype(int)]*np.conj(yf@voltage)
        st = voltage[case['branch'][:,1].astype(int)]*np.conj(yt@voltage)
        got = [complex(r['p'],r['q']) for r in result['branches']]
        np.testing.assert_allclose(got,np.column_stack((sf,st)).ravel(),rtol=0,atol=1e-11)

    def test_full_ybus_and_both_terminal_flows_against_pypower(self):
        self.check_admittances_and_validation(self.case,self.topo)

    def test_phase_shift_and_nonunit_tap_both_directions(self):
        self.case['branch'][0,8:10] = [1.07,17.]
        self.topo.lines[0] = replace(self.topo.lines[0],tap_ratio=1.07,shift_rad=np.deg2rad(17))
        self.check_admittances_and_validation(self.case,self.topo)
