"""Neighbor-only binary consensus exchange over an MPI distributed graph.

Replaces the former global JSON Allgather/Allgatherv: each rank communicates
only with ranks that share at least one consensus edge. Values are raw
float64, so the exchanged numbers are bit-identical to the former JSON path
(Python's float repr round-trips exactly).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

import numpy as np
from mpi4py import MPI

from distributed_opf.mpi_metrics import MeasuredComm

# Row layout sent for a consensus edge (i owned by the sender, j owned by the receiver):
# own V_i, own theta_i, sender's copy of V_j, copy of theta_j,
# u_own(i,j) for V and theta, u(i,j) for V and theta (both before this iteration's u update).
EDGE_FIELDS = ("v", "va", "v_copy", "va_copy", "u_v_own", "u_th_own", "u_v", "u_th")
VT_FIELDS = ("v", "va")


def canonical_edges(my_rank: int, edges: Iterable[Tuple[int, int, int]]) -> Dict[int, List[Tuple[int, int]]]:
    """Group (local bus, remote bus, remote rank) edges by rank in an order both endpoints agree on.

    Both sides sort a shared edge by (bus on the lower rank, bus on the higher rank),
    so row k sent by one endpoint is row k expected by the other.
    """
    by_rank: Dict[int, set] = {}
    for local_bus, remote_bus, remote_rank in edges:
        if int(remote_rank) == int(my_rank):
            raise ValueError("A consensus edge must connect two different ranks")
        by_rank.setdefault(int(remote_rank), set()).add((int(local_bus), int(remote_bus)))
    ordered: Dict[int, List[Tuple[int, int]]] = {}
    for rank, pairs in sorted(by_rank.items()):
        if my_rank < rank:
            ordered[rank] = sorted(pairs)
        else:
            ordered[rank] = sorted(pairs, key=lambda pair: (pair[1], pair[0]))
    return ordered


class NeighborExchange:
    def __init__(self, world, edges: Iterable[Tuple[int, int, int]]):
        raw = world.comm if isinstance(world, MeasuredComm) else world
        self.rank = int(raw.Get_rank())
        self.edges = canonical_edges(self.rank, edges)
        self.neighbor_ranks = list(self.edges)
        graph = raw.Create_dist_graph_adjacent(self.neighbor_ranks, self.neighbor_ranks, reorder=False)
        self.comm = MeasuredComm(graph)
        if isinstance(world, MeasuredComm):
            # One metrics table per rank: neighbor calls are reported next to world calls.
            self.comm.metrics = world.metrics
        self.counts = np.array([len(self.edges[r]) for r in self.neighbor_ranks], dtype="i")
        self.displs = np.concatenate(([0], np.cumsum(self.counts)[:-1])).astype("i") if len(self.counts) else np.zeros(0, dtype="i")
        self.rows = int(self.counts.sum())
        self._verify_edge_agreement()

    def _verify_edge_agreement(self) -> None:
        """Fail fast if two neighbors disagree on their shared edge list."""
        send = np.zeros((self.rows, 4), dtype="d")
        row = 0
        for rank in self.neighbor_ranks:
            for local_bus, remote_bus in self.edges[rank]:
                send[row] = (self.rank, rank, local_bus, remote_bus)
                row += 1
        received = self.exchange(send)
        row = 0
        for rank in self.neighbor_ranks:
            for local_bus, remote_bus in self.edges[rank]:
                expected = (rank, self.rank, remote_bus, local_bus)
                if tuple(int(x) for x in received[row]) != expected:
                    raise RuntimeError(f"Rank {self.rank}: consensus edge mismatch with rank {rank}: "
                                       f"expected {expected}, received {tuple(received[row])}")
                row += 1

    def exchange(self, send: np.ndarray) -> np.ndarray:
        """send: float64 array (rows, width) in edge order; returns the neighbors' rows in the same order."""
        send = np.ascontiguousarray(send, dtype="d")
        if send.ndim != 2 or send.shape[0] != self.rows:
            raise ValueError(f"expected {self.rows} rows, got shape {send.shape}")
        width = send.shape[1]
        recv = np.empty_like(send)
        if not self.neighbor_ranks:
            return recv
        counts, displs = self.counts * width, self.displs * width
        self.comm.Neighbor_alltoallv([send.reshape(-1), (counts, displs), MPI.DOUBLE],
                                     [recv.reshape(-1), (counts, displs), MPI.DOUBLE])
        return recv

    def close(self) -> None:
        try:
            self.comm.comm.Free()
        except Exception:
            pass
