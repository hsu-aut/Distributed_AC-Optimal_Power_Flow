import unittest
from pathlib import Path
import numpy as np
from distributed_opf.config import load_topology_config
from distributed_opf.mpi_metrics import MeasuredComm, buffer_bytes

ROOT = Path(__file__).resolve().parents[1]


class MeasurementAndDataTests(unittest.TestCase):
    def test_transformer_base_and_resistance(self):
        old = load_topology_config(ROOT / "legacy" / "topologies" / "topology_ieee118_aligned.yaml")
        new = load_topology_config(ROOT / "legacy" / "topologies" / "topology_ieee118_trafo_corrected.yaml")
        self.assertEqual(len(new.lines), 186)
        self.assertEqual(old.buses, new.buses)
        changed = [(a, b) for a, b in zip(old.lines, new.lines) if a != b]
        self.assertEqual(len(changed), 13)
        for a, b in changed:
            self.assertAlmostEqual(np.hypot(b.r_pu, b.x_pu), a.x_pu/99)
        end = next(l for l in new.lines if (l.from_bus, l.to_bus) == (115, 67))
        self.assertAlmostEqual(end.r_pu, 0.00034)
        self.assertAlmostEqual(end.x_pu, 0.00405)

    def test_mpi_buffer_accounting_and_call_forwarding(self):
        class FakeComm:
            def Allgather(self, send, recv):
                recv[:] = send[0]
                return "result"
        comm = MeasuredComm(FakeComm())
        send, recv = np.ones(1), np.zeros(4)
        self.assertEqual(comm.Allgather(send, recv), "result")
        self.assertTrue(np.all(recv == 1))
        self.assertEqual(comm.metrics["Allgather"]["input_buffer_bytes"], 8)
        self.assertEqual(comm.metrics["Allgather"]["output_buffer_bytes"], 32)
        self.assertEqual(buffer_bytes([send, "datatype"]), 8)
        self.assertEqual(buffer_bytes({"object": "not a buffer"}), 0)

    def test_mpi_error_is_propagated_and_counted(self):
        class FakeComm:
            def Barrier(self):
                raise RuntimeError("failure")
        comm = MeasuredComm(FakeComm())
        with self.assertRaises(RuntimeError):
            comm.Barrier()
        self.assertEqual(comm.metrics["Barrier"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
