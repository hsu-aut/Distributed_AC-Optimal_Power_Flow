import math
import unittest
from pathlib import Path

from distributed_opf.partitioning import partition_graph_aware
from distributed_opf.config import load_topology_config
from distributed_opf.partitioning import partition_connected_impedance, partition_statistics

ROOT = Path(__file__).resolve().parents[1]


class ConnectedImpedancePartitioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topology = load_topology_config(ROOT / "topology_ieee118_full.yaml")
        cls.buses = sorted(cls.topology.buses)

    def test_partitions_are_complete_connected_balanced_and_keep_stiff_leaf_branch(self):
        for k in (2, 3, 4, 8, 16):
            with self.subTest(k=k):
                assignment = partition_connected_impedance(self.buses, self.topology, k)
                self.assertEqual(len(assignment), k)
                self.assertEqual(sorted(b for bs in assignment.values() for b in bs), self.buses)
                stats = partition_statistics(assignment, self.topology)
                self.assertEqual(stats["disconnected_partitions"], 0)
                self.assertEqual(stats["leaf_bus_cuts"], 0)
                self.assertLessEqual(max(stats["sizes"]), math.ceil(len(self.buses) / k * 1.2))
                owner = {b: r for r, bs in assignment.items() for b in bs}
                # Stiffest branch of the case (|y| = 246 p.u.) to a leaf bus with generator.
                self.assertEqual(owner[67], owner[115])

    def test_deterministic(self):
        self.assertEqual(partition_connected_impedance(self.buses, self.topology, 8),
                         partition_connected_impedance(self.buses, self.topology, 8))

    def test_lower_cut_admittance_than_previous_partitioner(self):
        for k in (4, 8, 16):
            with self.subTest(k=k):
                new = partition_statistics(partition_connected_impedance(self.buses, self.topology, k), self.topology)
                old = partition_statistics(partition_graph_aware(self.buses, self.topology, k), self.topology)
                self.assertLess(new["sum_cut_admittance"], old["sum_cut_admittance"])
                self.assertGreater(old["disconnected_partitions"], 0)

    def test_trivial_cases(self):
        self.assertEqual(partition_connected_impedance(self.buses, self.topology, 1), {0: self.buses})
        self.assertEqual(len(partition_connected_impedance(self.buses, self.topology, 118)), 118)


if __name__ == "__main__":
    unittest.main()
