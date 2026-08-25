#!/bin/bash
# Supervisor: keep retrying the full D11 qualification chain until success
# or 24h budget exhaustion.  Survives site-network outages that kill any
# single attempt; cleans orphaned scheduler jobs before every retry.
set -u
cd "$(dirname "$0")"

LOG="/tmp/qual-supervise.log"
DEADLINE=$((SECONDS + 86400))   # 24 h overall budget
attempt=0

log() { echo "[supervisor $(date +%H:%M:%S)] $*" >> "$LOG"; }

log "supervisor started (pid $$)"
while [ $SECONDS -lt $DEADLINE ]; do
  attempt=$((attempt + 1))
  log "=== attempt $attempt ==="

  # clear orphaned dispatcher jobs left by dead attempts
  ssh -o ConnectTimeout=15 <site-alias> \
    'squeue -u <site-user> -h -o "%i" | xargs -r scancel' >/dev/null 2>&1
  sleep 5

  ../dftworld2/.venv/bin/python scripts/infra/qualify_hpc_dispatcher.py \
    --phase canary >> "$LOG" 2>&1
  rc1=$?
  log "canary rc=$rc1"
  if [ $rc1 -ne 0 ]; then
    log "sleep 600 before retry"
    sleep 600
    continue
  fi

  ../dftworld2/.venv/bin/python scripts/infra/qualify_hpc_dispatcher.py \
    --phase cp2k --cp2k-lock reference/cp2k-runtime.lock.json \
    --authorized >> "$LOG" 2>&1
  rc2=$?
  log "cp2k rc=$rc2"
  if [ $rc2 -eq 0 ]; then
    log "SUCCESS — D11 chain complete"
    echo "SUPERVISOR_SUCCESS" >> "$LOG"
    exit 0
  fi
  sleep 600
done
log "budget exhausted without success"
echo "SUPERVISOR_FAILED" >> "$LOG"
exit 1
