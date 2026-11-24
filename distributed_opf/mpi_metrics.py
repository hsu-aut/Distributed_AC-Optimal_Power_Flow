"""Rank-local blocking MPI call metrics; buffer bytes are not network bytes."""
import time
from mpi4py import MPI


def buffer_bytes(value):
    if isinstance(value, (list, tuple)):
        return buffer_bytes(value[0]) if value else 0
    return int(getattr(value, "nbytes", 0))


class MeasuredComm:
    METHODS = {"Allgather", "Allgatherv", "Allreduce", "Barrier", "Bcast", "Gather",
               "allgather", "allreduce", "gather", "bcast", "barrier",
               "Neighbor_alltoallv"}

    def __init__(self, comm):
        self.comm = comm
        self.metrics = {}

    def __getattr__(self, name):
        method = getattr(self.comm, name)
        if name not in self.METHODS:
            return method

        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                row = self.metrics.setdefault(name, {"calls": 0, "wall_s": 0., "input_buffer_bytes": 0, "output_buffer_bytes": 0})
                row["calls"] += 1
                row["wall_s"] += time.perf_counter()-start
                if name[0].isupper() and args:
                    row["input_buffer_bytes"] += buffer_bytes(args[0])
                    if len(args) > 1:
                        row["output_buffer_bytes"] += buffer_bytes(args[1])
        return measured


_WORLD = None


def measured_world():
    global _WORLD
    if _WORLD is None:
        _WORLD = MeasuredComm(MPI.COMM_WORLD)
    return _WORLD
