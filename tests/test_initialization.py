import json
import tempfile
import unittest
from pathlib import Path

from distributed_opf.config import load_topology_config
from distributed_opf.initialization import (build_initial_state, load_central_reference, read_initial_state,
                                            write_initial_state)

ROOT = Path(__file__).resolve().parents[1]


class InitializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topology = load_topology_config(ROOT / "topology_ieee118_full.yaml")
        cls.reference = {b: {"v": 1.02, "va": 0.1 * (b % 7), "pg": (bus.pg_min + bus.pg_max) / 2,
                             "qg": (bus.qg_min + bus.qg_max) / 2}
                         for b, bus in cls.topology.buses.items()}

    def test_unperturbed_reference_is_copied(self):
        state = build_initial_state(self.topology, self.reference)
        for b, bus in self.topology.buses.items():
            self.assertEqual(state[b]["v"], min(bus.v_max_pu, max(bus.v_min_pu, 1.02)))
            self.assertEqual(state[b]["va"], 0.0 if bus.is_slack else self.reference[b]["va"])
            if not bus.is_generator:
                self.assertEqual((state[b]["pg"], state[b]["qg"]), (0.0, 0.0))

    def test_perturbation_is_seeded_and_within_bounds(self):
        a = build_initial_state(self.topology, self.reference, 0.05, seed=3)
        b = build_initial_state(self.topology, self.reference, 0.05, seed=3)
        c = build_initial_state(self.topology, self.reference, 0.05, seed=4)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        for bus_id, bus in self.topology.buses.items():
            s = a[bus_id]
            self.assertTrue(bus.v_min_pu <= s["v"] <= bus.v_max_pu)
            if bus.is_slack:
                self.assertEqual(s["va"], 0.0)
            if bus.is_generator:
                self.assertTrue(bus.pg_min <= s["pg"] <= bus.pg_max)
                self.assertTrue(bus.qg_min <= s["qg"] <= bus.qg_max)

    def test_round_trip_and_central_result_format(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            state = build_initial_state(self.topology, self.reference, 0.01, seed=1)
            write_initial_state(folder / "init.json", state)
            self.assertEqual(read_initial_state(folder / "init.json"), state)
            central = {"success": True, "central": {
                "v": {str(b): s["v"] for b, s in self.reference.items()},
                "theta": {str(b): s["va"] for b, s in self.reference.items()},
                "pg": {str(b): s["pg"] for b, s in self.reference.items()},
                "qg": {str(b): s["qg"] for b, s in self.reference.items()}}}
            (folder / "result.json").write_text(json.dumps(central))
            self.assertEqual(load_central_reference(folder), self.reference)


if __name__ == "__main__":
    unittest.main()
