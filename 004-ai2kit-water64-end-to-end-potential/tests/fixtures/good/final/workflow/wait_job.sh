#!/bin/bash
# ============================================================================
# wait_job.sh — poll pseudo-slurm until a batch job reaches a terminal state.
#
# Usage: wait_job.sh <jobid> [timeout_seconds] [script_path]
#
# Implements the pseudo-slurm contract (CONTRACT.md §3):
#   - `sacct -X -P --format=JobID,State -j <id>` returns CSV `JobID|State`
#     with full state names PENDING / RUNNING / COMPLETED / FAILED / CANCELLED.
#   - sacct resolves state itself: process alive -> RUNNING; else
#     `<script>.exitcode` == 0 -> COMPLETED; else FAILED; unknown id -> omit row.
#   - When sacct omits a row, omb falls back to squeue.
# If both sacct and squeue stop listing the job we fall back to the oh-my-batch
# `.exitcode` file when a script path was supplied (belt-and-suspenders).
# ============================================================================
set -u

JOBID="${1:-}"
TIMEOUT="${2:-86400}"
SCRIPT="${3:-}"

if [ -z "$JOBID" ]; then
  echo "wait_job: missing job id" >&2
  exit 2
fi

INTERVAL="${WAIT_JOB_INTERVAL:-5}"
elapsed=0

while [ "$elapsed" -lt "$TIMEOUT" ]; do
  # 1) Ask sacct for a terminal state -------------------------------------
  STATES="$(sacct -X -P --format=JobID,State -j "$JOBID" 2>/dev/null | awk -F'|' 'NR>1{print $2}')"

  if [ -n "$STATES" ]; then
    all_terminal=1
    for st in $STATES; do
      case "$st" in
        COMPLETED) ;;
        FAILED|CANCELLED|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY|BOOT_FAIL|DEADLINE|PREEMPTED)
          echo "wait_job: job $JOBID -> $st" >&2
          exit 1
          ;;
        *)
          # PENDING / RUNNING / CONFIGURING / COMPLETING / REQUEUED / ...
          all_terminal=0
          ;;
      esac
    done
    if [ "$all_terminal" = "1" ]; then
      echo "wait_job: job $JOBID COMPLETED after ${elapsed}s"
      exit 0
    fi
  else
    # 2) sacct returned nothing -> check squeue ----------------------------
    if squeue -h -o "%A %t" 2>/dev/null | awk '{print $1}' | grep -qw "$JOBID"; then
      : # still queued/running
    else
      # 3) gone from both -> honor the .exitcode fallback if we know the script
      if [ -n "$SCRIPT" ] && [ -f "$SCRIPT.exitcode" ]; then
        rc="$(cat "$SCRIPT.exitcode" 2>/dev/null | tr -d '[:space:]')"
        if [ "$rc" = "0" ]; then
          echo "wait_job: job $JOBID COMPLETED via .exitcode after ${elapsed}s"
          exit 0
        else
          echo "wait_job: job $JOBID FAILED via .exitcode (rc=$rc)" >&2
          exit 1
        fi
      fi
      # Unknown / racing between reap and record — allow a short grace period
      if [ "$elapsed" -gt 60 ]; then
        echo "wait_job: job $JOBID vanished from scheduler without terminal state" >&2
        exit 1
      fi
    fi
  fi

  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done

echo "wait_job: timeout after ${TIMEOUT}s for job $JOBID" >&2
exit 1
