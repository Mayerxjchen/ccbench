#!/bin/bash
# Detached qualification runner: canary -> CP2K merge, full log to /tmp/qual-<date>.log
set -uo pipefail
cd "$(dirname "$0")"

LOG="/tmp/qual-run-$(date +%H%M).log"
echo "log=$LOG" >&2

# clear orphaned dispatcher jobs from dead chains
ssh -o ConnectTimeout=15 <site-alias> 'squeue -u <site-user> -h -o "%i" | xargs -r scancel' 2>/dev/null

nohup bash -c '
  ../dftworld2/.venv/bin/python scripts/infra/qualify_hpc_dispatcher.py --phase canary
  echo "=== CP2K PHASE ==="
  ../dftworld2/.venv/bin/python scripts/infra/qualify_hpc_dispatcher.py --phase cp2k \
    --cp2k-lock reference/cp2k-runtime.lock.json --authorized
' >> "$LOG" 2>&1 &

echo "launched pid=$! log=$LOG"
