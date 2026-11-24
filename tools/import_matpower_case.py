import argparse
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distributed_opf.matpower_case import export_topology

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/matpower/case118.m")
    parser.add_argument("--out", default="topology_ieee118_full.yaml")
    args = parser.parse_args()
    topology = export_topology(args.source)
    Path(args.out).write_text(yaml.safe_dump(topology, sort_keys=False), encoding="utf-8")
    print(f"{len(topology['buses'])} buses, {len(topology['lines'])} branches; SHA256 {topology['source_sha256']}")
