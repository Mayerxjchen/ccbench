#!/bin/bash
# Common verifier launcher: python -m ccbench.verifiers.launcher

# ============================================================================
# 042 verifier entry (harness contract: /tests/test.sh -> result.json).
#
# Runs tests/verifier.py's verify() against the agent workspace (/app, or
# $AI2KIT_042_SUBMISSION for dev smoke). Reward 1.0 iff V0-V6 all pass.
# Emits the COMMON result.json (schemas/result.schema.json) into /logs/verifier/
# and the legacy reward.txt compatibility fallback.
#
# Staged layout inside the container:
#   /tests/test.sh  /tests/verifier.py  /tests/test_outputs.py
#   /tests/hidden/  (hidden-frames/, density-reference.json, thresholds.json)
# Hidden paths are injected via task.toml [verifier.env]; verifier.py defaults
# to /tests/hidden/* when absent.
#
# Classification:
#   reward=1  -> VALID_RESULT / PASS       (counted scientifically)
#   reward=0  -> AGENT_FAILURE / SCIENTIFIC_FAIL
# ============================================================================
set -u
PY=python3
for cand in /opt/deepmd-jax/bin/python /opt/ai2kit/bin/python /usr/local/bin/python3; do
  [ -x "$cand" ] && PY="$cand" && break
done
mkdir -p /logs/verifier 2>/dev/null || true

cd /tests || exit 1
SUB="${AI2KIT_042_SUBMISSION:-/app}"
if "$PY" verifier.py "$SUB"; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "PASS",
  "reason": "042 V0-V6 scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[042 test.sh] reward=1 result.json=VALID_RESULT"
  exit 0
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "AGENT_FAILURE",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "042 V0-V6 scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[042 test.sh] reward=0 result.json=AGENT_FAILURE"
  exit 1
fi
