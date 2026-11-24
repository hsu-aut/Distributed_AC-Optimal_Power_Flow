import json
import unittest

import numpy as np

from distributed_opf.admm_ac import AcSubnetResult, decode_subnet_result, encode_subnet_result
from distributed_opf.agent_ac_mpi import TIMING_PHASES, timing_statistics
from distributed_opf.mpi_metrics import MeasuredComm
from distributed_opf.neighbor_exchange import canonical_edges


class NeighborExchangeTests(unittest.TestCase):
    def test_both_endpoints_agree_on_row_order(self):
        # Rank 0 owns buses 1 and 2, rank 1 owns buses 7 and 9; three cut edges plus a parallel duplicate.
        rank0 = canonical_edges(0, [(2, 7, 1), (1, 9, 1), (1, 7, 1), (1, 7, 1)])
        rank1 = canonical_edges(1, [(7, 2, 0), (9, 1, 0), (7, 1, 0)])
        self.assertEqual(rank0[1], [(1, 7), (1, 9), (2, 7)])
        self.assertEqual([(remote, local) for local, remote in rank1[0]], rank0[1])

    def test_edge_to_own_rank_is_rejected(self):
        with self.assertRaises(ValueError):
            canonical_edges(3, [(1, 2, 3)])

    def test_neighbor_collective_is_measured(self):
        class FakeComm:
            def Neighbor_alltoallv(self, send, recv):
                recv[0][:] = send[0]
        comm = MeasuredComm(FakeComm())
        send, recv = np.arange(4.0), np.zeros(4)
        comm.Neighbor_alltoallv([send, None], [recv, None])
        self.assertTrue(np.array_equal(recv, send))
        self.assertEqual(comm.metrics["Neighbor_alltoallv"]["input_buffer_bytes"], 32)

    def test_subnet_warm_start_round_trip_is_exact(self):
        value = 0.1 + 0.2  # not exactly representable in decimal
        result = AcSubnetResult(
            v={3: value}, va={3: -value}, pg={3: 1.5}, qg={3: 0.25},
            pij={(7, 3, 4): value / 3}, qij={(7, 3, 4): -1e-17},
            objective_cost=12.5, generation_cost=10.0, p_slack={3: 0.0}, q_slack={3: 0.0},
            line_slack_max=0.0, line_slack_mean=0.0, gen_slack_max=0.0,
            v_copy={4: 1.0 + 1e-15}, va_copy={4: value})
        decoded = decode_subnet_result(json.loads(json.dumps(encode_subnet_result(result))))
        self.assertEqual(decoded, result)
        self.assertIsNone(decode_subnet_result(encode_subnet_result(None)))

    def test_timing_statistics(self):
        stats = timing_statistics({name: [] for name in TIMING_PHASES} | {"x_solve": [1.0, 2.0, 3.0]})
        self.assertEqual(stats["x_solve"]["total_s"], 6.0)
        self.assertEqual(stats["x_solve"]["max_s"], 3.0)
        self.assertEqual(stats["io"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
