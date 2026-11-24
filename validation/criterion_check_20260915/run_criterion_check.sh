#!/usr/bin/env bash
# Stopping-criterion comparison: frozen campaign configurations run with a tighter tolerance so that
# the residual trace covers the campaign levels for both conventions (solver per-rank gate and Boyd
# matrix form, recorded side by side). Quotients scale exactly with the tolerance
# (tools/criterion_comparison.py). Partitions at 2e-6 (levels 1e-5..1e-4), 118 bus agents at 2e-5 (level 1e-4).
set -u
REPO=/mnt/c/Users/User/distributed_opf
OUT=$REPO/results/criterion_check_20260915
PY=/home/user/miniconda3/envs/distributed_opf/bin/python
export PATH="$(dirname "$PY"):$PATH"
cd "$REPO"
run() {
  name="$1"; shift
  echo "=== $name $(date -Is)"
  $PY tools/run_ac_local.py --centered-penalty --solve-limit 0 --threads 1 --log-every 0 --monitor-every 0 \
    --timing-trace --checkpoint-every 1000 "$@" --scratch --out "$OUT/$name" > "$OUT/$name.driver.log" 2>&1
  echo "exit=$? $(date -Is)"
}
P="--partitioner connected_impedance --rho 3e5 --abs-tol 2e-6 --rel-tol 2e-6 --timeout 7200"
run p3  $P --agents 3  --batch 3  --iterations 20000
run p4  $P --agents 4  --batch 4  --iterations 20000
run p8  $P --agents 8  --batch 8  --iterations 20000
run p16 $P --agents 16 --batch 16 --iterations 30000
run bus118_edge --agents 118 --batch 118 --edge-rho admittance --edge-rho-exponent 1 --rho 1e6 \
  --abs-tol 2e-5 --rel-tol 2e-5 --iterations 60000 --mpi-yield --timeout 21600
echo "=== done $(date -Is)"
