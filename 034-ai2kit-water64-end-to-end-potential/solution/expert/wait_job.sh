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
#   - Fail-closed state handling (Task 4):
#       * the PARENT job row wins over step/array-child rows (42.0 / 42_1);
#         the awk match is an anchored STRING compare so `42.0` never matches `42`
#       * a state suffix (`COMPLETED+`) or cancellation reason
#         (`CANCELLED by <uid>`) is stripped to the canonical token
#       * vanishing from both sacct and squeue without a terminal state is a
#         FAILURE (after a 60 s grace), never a success
#       * a controller timeout cancels the job instead of leaving an orphan
# If both sacct and squeue stop listing the job we fall back to the oh-my-batch
# `.exitcode` file when a script path was supplied (belt-and-suspenders).
#
# Poll interval: WAIT_JOB_INTERVAL, default 60 s (HPC cadence).  The container
# env.sh overrides this to 5 s for the fast pseudo-slurm test-bed.
# ============================================================================
set -u

JOBID="${1:-}"
TIMEOUT="${2:-86400}"
SCRIPT="${3:-}"

if [ -z "$JOBID" ]; then
  echo "wait_job: missing job id" >&2
  exit 2
fi

INTERVAL="${WAIT_JOB_INTERVAL:-60}"
elapsed=0

while [ "$elapsed" -lt "$TIMEOUT" ]; do
  # 1) Ask sacct for a terminal state -------------------------------------
  # Anchored string match ("^42$"): the parent row is authoritative; step rows
  # (42.0 / 42.batch) and array children (42_1) must never be mistaken for the
  # job itself.  Numeric `==` would coerce "42.0" to 42 and mis-read a step row.
  RAW_STATE="$(sacct -X -P --format=JobID,State -j "$JOBID" 2>/dev/null | awk -F'|' -v id="$JOBID" '$1 ~ ("^" id "$") {print $2; exit}')"
  STATE="${RAW_STATE%% *}"
  STATE="${STATE%+}"

  if [ -n "$STATE" ]; then
    case "$STATE" in
      COMPLETED)
      echo "wait_job: job $JOBID COMPLETED after ${elapsed}s"
      exit 0
      ;;
      FAILED|CANCELLED|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY|BOOT_FAIL|DEADLINE|PREEMPTED|REVOKED)
        echo "wait_job: job $JOBID -> $RAW_STATE" >&2
        exit 1
        ;;
      *)
        : # PENDING / RUNNING / CONFIGURING / COMPLETING / REQUEUED / ...
        ;;
    esac
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

scancel "$JOBID" >/dev/null 2>&1 || true
echo "wait_job: timeout after ${TIMEOUT}s for job $JOBID" >&2
exit 1
