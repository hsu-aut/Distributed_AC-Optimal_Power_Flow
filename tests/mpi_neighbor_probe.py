"""Run with: mpirun -np 4 python tests/mpi_neighbor_probe.py

Ring of ranks, bus id = 10*rank, plus a second bus on even ranks so that a
rank pair shares more than one edge. Each rank must receive exactly the rows
its neighbors packed for the shared edges.
"""
import sys
from pathlib import Path

import numpy as np
from mpi4py import MPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distributed_opf.mpi_metrics import measured_world
from distributed_opf.neighbor_exchange import NeighborExchange


def buses(rank):
    return [10 * rank, 10 * rank + 1] if rank % 2 == 0 else [10 * rank]


def main():
    world = measured_world()
    rank, size = world.Get_rank(), world.Get_size()
    edges = []
    for other in {(rank - 1) % size, (rank + 1) % size} - {rank}:
        for local in buses(rank):
            for remote in buses(other):
                edges.append((local, remote, other))
    exchange = NeighborExchange(world, edges)
    rows = [(r, local, remote) for r in exchange.neighbor_ranks for local, remote in exchange.edges[r]]
    send = np.array([[rank, local, remote, local * 1e-3 + 1 / 3] for _, local, remote in rows], dtype="d").reshape(-1, 4)
    recv = exchange.exchange(send)
    for (other, local, remote), row in zip(rows, recv):
        expected = [other, remote, local, remote * 1e-3 + 1 / 3]
        if row.tolist() != expected:
            raise AssertionError(f"rank {rank}: got {row.tolist()}, expected {expected}")
    ok = world.allreduce(1, op=MPI.SUM)
    if rank == 0:
        calls = world.metrics.get("Neighbor_alltoallv", {}).get("calls", 0)
        print(f"neighbor probe ok on {ok} ranks; neighbor collective calls on rank 0: {calls}")
    exchange.close()


if __name__ == "__main__":
    main()
