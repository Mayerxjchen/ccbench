#!/bin/bash
# Common verifier launcher: python -m bench.verifiers.launcher
# ============================================================================
# 033 verifier entry (harness contract: /tests/test.sh -> result.json).
#
# The graded submission must pass test_outputs.py (domain-wall-search
# scientific gates).  The verifier emits the COMMON result.json
# (schemas/result.schema.json) into /logs/verifier/ while reward.txt stays as
# a legacy compatibility fallback; the common harness reads result.json first.
#
# Classification:
#   pytest passes  -> VALID_RESULT / PASS        (counted scientifically)
#   pytest fails   -> AGENT_FAILURE / SCIENTIFIC_FAIL
# ============================================================================
set -u
PY=/opt/matclaw/bin/python
if [ ! -x "$PY" ]; then
  # dev fallback: host python with ase/deepmd on PATH
  PY=python3
fi
mkdir -p /logs/verifier 2>/dev/null || true

cd /tests || exit 1
if "$PY" -m pytest -q /tests/test_outputs.py -rA; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "PASS",
  "reason": "033 domain-wall-search scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[033 test.sh] reward=1 result.json=VALID_RESULT"
  exit 0
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "AGENT_FAILURE",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "033 domain-wall-search scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[033 test.sh] reward=0 result.json=AGENT_FAILURE"
  exit 1
fi
