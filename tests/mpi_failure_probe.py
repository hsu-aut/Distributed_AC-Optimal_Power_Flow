"""Integration probe: run with mpirun -np 2; must exit nonzero, not hang.

Rank zero fails in Agent.run while its peer waits in a collective.
No optimization or license is needed.
"""
from pathlib import Path
import runpy
import sys
from types import ModuleType
from mpi4py import MPI

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))


class FailingAgent:
    def __init__(self, **kwargs):
        pass

    def run(self):
        if MPI.COMM_WORLD.rank == 0:
            raise RuntimeError("intentional MPI failure probe")
        MPI.COMM_WORLD.Barrier()


module = ModuleType("distributed_opf.agent_ac_mpi")
module.AcAgent = FailingAgent
sys.modules[module.__name__] = module
sys.argv = ["run_mpi_cluster.py", "--mode", "ac", "--topology",
            str(root / "topology_3bus.yaml"), "--buses", "0", "1"]
runpy.run_path(str(root / "run_mpi_cluster.py"), run_name="__main__")
