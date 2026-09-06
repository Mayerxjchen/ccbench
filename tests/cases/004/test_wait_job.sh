#!/bin/bash
# ============================================================================
# Offline tests for solution/expert/wait_job.sh against stubbed scheduler
# commands (sacct / squeue / scancel / sleep on PATH).  No Docker, no real
# Slurm.  Task 4: state suffixes, terminal failures, parent-job-row
# preference, vanish-without-terminal (fail-closed), .exitcode fallback,
# timeout cancellation, and the 60 s HPC polling default.
#
# Usage: bash tests/test_wait_job.sh
# ============================================================================
set -u

EXPERT="$(cd "$(dirname "$0")/../solution/expert" && pwd)"
WAIT_SCRIPT="$EXPERT/wait_job.sh"
ENV_HPC="$EXPERT/env-hpc.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/034-wait-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"

cat > "$TMP/bin/sacct" <<'EOF'
#!/bin/bash
cat "${SACCT_OUT_FILE:-/dev/null}"
EOF
cat > "$TMP/bin/squeue" <<'EOF'
#!/bin/bash
cat "${SQUEUE_OUT_FILE:-/dev/null}"
EOF
cat > "$TMP/bin/scancel" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "${SCANCEL_LOG_FILE:-/dev/null}"
EOF
cat > "$TMP/bin/sleep" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$TMP/bin/"*

PASS=0
FAIL=0
WAIT_JOB_INTERVAL=""   # empty => wait_job uses its own default (60 s)

# run_wait <jobid> <timeout> [script]  — reads $TMP/sacct.out, $TMP/squeue.out;
# honors the shell var WAIT_JOB_INTERVAL only when it is non-empty.
run_wait() {
  local cmd=(env PATH="$TMP/bin:$PATH" SACCT_OUT_FILE="$TMP/sacct.out"
             SQUEUE_OUT_FILE="$TMP/squeue.out" SCANCEL_LOG_FILE="$TMP/scancel.log")
  [ -n "$WAIT_JOB_INTERVAL" ] && cmd+=(WAIT_JOB_INTERVAL="$WAIT_JOB_INTERVAL")
  "${cmd[@]}" bash "$WAIT_SCRIPT" "$1" "$2" "${3:-}" >/dev/null 2>&1
}

check() {  # check <desc> <expected_rc> <actual_rc>
  if [ "$3" -eq "$2" ]; then
    echo "ok   - $1"
    PASS=$((PASS + 1))
  else
    echo "FAIL - $1 (rc=$3 want $2)"
    FAIL=$((FAIL + 1))
  fi
}

state() {    # state <sacct-row>      e.g. '42|COMPLETED'
  printf 'JobID|State\n%s\n' "$1" > "$TMP/sacct.out"
  : > "$TMP/squeue.out"
}
no_state() { : > "$TMP/sacct.out"; : > "$TMP/squeue.out"; }

# --- terminal states ---------------------------------------------------------
state "42|COMPLETED"
run_wait 42 100
check "COMPLETED -> 0" 0 $?
state "42|COMPLETED+"
run_wait 42 100
check "COMPLETED+ suffix -> 0" 0 $?
state "42|FAILED"
run_wait 42 100
check "FAILED -> 1" 1 $?
state "42|CANCELLED"
run_wait 42 100
check "CANCELLED -> 1" 1 $?
state "42|CANCELLED by root"
run_wait 42 100
check "CANCELLED by uid -> 1" 1 $?
state "42|TIMEOUT"
run_wait 42 100
check "TIMEOUT -> 1" 1 $?
state "42|OUT_OF_MEMORY"
run_wait 42 100
check "OUT_OF_MEMORY -> 1" 1 $?

# --- parent job row wins over step / array-child rows ------------------------
printf 'JobID|State\n42.0|FAILED\n42|COMPLETED\n' > "$TMP/sacct.out"
: > "$TMP/squeue.out"
run_wait 42 100
check "step row first, parent row wins -> 0" 0 $?
printf 'JobID|State\n42_1|RUNNING\n42.batch|FAILED\n42|COMPLETED+\n' > "$TMP/sacct.out"
: > "$TMP/squeue.out"
run_wait 42 100
check "array child + step rows, parent wins -> 0" 0 $?

# --- vanish without a terminal state is a FAILURE (after 60 s grace) ---------
no_state
WAIT_JOB_INTERVAL=61 run_wait 42 100
check "vanished without terminal -> 1" 1 $?

# --- squeue keeps a job alive that sacct no longer lists ---------------------
no_state
printf '42 R\n' > "$TMP/squeue.out"
WAIT_JOB_INTERVAL=61 run_wait 42 100
check "squeue fallback keeps waiting -> timeout (1)" 1 $?
[ -s "$TMP/scancel.log" ]
check "squeue-visible job cancels on timeout" 0 $?
: > "$TMP/scancel.log"

# --- .exitcode fallback (belt-and-suspenders) --------------------------------
mkdir -p "$TMP/w"
printf '0\n' > "$TMP/w/e.exitcode"
no_state
WAIT_JOB_INTERVAL=1 run_wait 42 100 "$TMP/w/e"
check ".exitcode=0 -> COMPLETED (0)" 0 $?
printf '7\n' > "$TMP/w/e.exitcode"
no_state
WAIT_JOB_INTERVAL=1 run_wait 42 100 "$TMP/w/e"
check ".exitcode=7 -> FAILED (1)" 1 $?

# --- controller timeout CANCELS the job (never orphans) ----------------------
state "42|RUNNING"
: > "$TMP/scancel.log"
WAIT_JOB_INTERVAL=61 run_wait 77 30
rc=$?
check "RUNNING until timeout -> 1" 1 "$rc"
[ "$(cat "$TMP/scancel.log")" = "77" ]
check "scancel called with the job id" 0 $?

# --- 60 s HPC polling default ------------------------------------------------
grep -q 'WAIT_JOB_INTERVAL:-60' "$WAIT_SCRIPT" || {
  echo "FAIL - wait_job default interval is 60 s"; FAIL=$((FAIL + 1)); }
grep -q 'WAIT_JOB_INTERVAL:-60' "$ENV_HPC" || {
  echo "FAIL - env-hpc default interval is 60 s"; FAIL=$((FAIL + 1)); }

echo "wait_job offline tests: $PASS ok, $FAIL failed"
[ "$FAIL" -eq 0 ]
