#!/usr/bin/env bash
# Hidden-verifier container entry (harness contract: /tests/test.sh ->
# /logs/verifier/result.json). This file runs ONLY the runtime submission
# verifier tests/verifier.py against the sealed submission. Case-construction
# self-tests (tests/test_verifier_contract.py, tools/ linters) are invoked by
# the builder at build time and are NEVER wired in here.
#
# Mount layout assumed at runtime:
#   /tests/test.sh  /tests/verifier.py  /tests/hidden/...
#   /submission (or /app)        sealed candidate submission (read-only)
#   /logs/verifier/result.json   the only graded output (schema: the common
#                                result contract; always written, pass or fail)
# Local smoke override (used by tests/test_verifier_contract.py and
# check_discovery_runnable.py; the script never depends on the case root):
#   SUBMISSION_ROOT=<dir> RESULT_DIR=<dir> BENCH_RUN_ID=<id> bash tests/test.sh
#
# Classification (never caller-controlled):
#   verifier exit 0 -> VALID_RESULT  / PASS             reward 1
#   verifier exit 1 -> AGENT_FAILURE / SCIENTIFIC_*     reward 0
#   anything else   -> INFRA_INVALID / VERIFIER_FAILURE  reward 0, retryable
set -u

TESTS_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY=python3
for cand in /opt/matclaw/bin/python /opt/ai2kit/bin/python /opt/deepmd-jax/bin/python /usr/local/bin/python3; do
  [ -x "$cand" ] && PY="$cand" && break
done

SUBMISSION="${SUBMISSION_ROOT:-}"
if [ -z "$SUBMISSION" ]; then
  for cand in /submission /app; do
    [ -d "$cand" ] && SUBMISSION="$cand" && break
  done
fi

RESULT_DIR="${RESULT_DIR:-/logs/verifier}"
mkdir -p "$RESULT_DIR" 2>/dev/null || true
RUN_ID="${BENCH_RUN_ID:-mlp-mvp}"

fallback_result() {
  # $1 result_class  $2 failure_code  $3 reason
  cat > "$RESULT_DIR/result.json" <<JSON
{
  "run_id": "$RUN_ID",
  "result_class": "$1",
  "failure_code": "$2",
  "reason": "$3",
  "retryable": true,
  "is_counted_scientifically": false
}
JSON
  echo 0 > "$RESULT_DIR/reward.txt"
  echo "[test.sh] result.json=$1/$2 (fallback)"
}

if [ ! -f "$TESTS_DIR/verifier.py" ]; then
  fallback_result INFRA_INVALID HARNESS_FAILURE "tests/verifier.py missing from the mounted /tests"
  exit 2
fi
if [ -z "$SUBMISSION" ]; then
  fallback_result INFRA_INVALID HARNESS_FAILURE "no sealed submission mounted (/submission or /app; set SUBMISSION_ROOT)"
  exit 2
fi

"$PY" "$TESTS_DIR/verifier.py" --submission "$SUBMISSION" --result-dir "$RESULT_DIR" --run-id "$RUN_ID"
code=$?

case "$code" in
  0)
    echo 1 > "$RESULT_DIR/reward.txt"
    echo "[test.sh] verifier passed; reward=1"
    exit 0
    ;;
  1)
    echo 0 > "$RESULT_DIR/reward.txt"
    echo "[test.sh] agent failure; reward=0"
    exit 1
    ;;
  *)
    echo 0 > "$RESULT_DIR/reward.txt"
    if [ ! -f "$RESULT_DIR/result.json" ]; then
      fallback_result INFRA_INVALID VERIFIER_FAILURE "verifier exited with $code without writing result.json"
    fi
    exit 2
    ;;
esac
