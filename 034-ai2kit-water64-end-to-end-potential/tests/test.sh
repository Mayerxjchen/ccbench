#!/bin/bash
# Common verifier launcher: python -m dftworld_bench.verifiers.launcher

# ============================================================================
# 034 verifier entry (harness contract: /tests/test.sh -> result.json).
#
# Runs the pytest suite against the agent workspace (/app, or
# AI2KIT_034_SUBMISSION for dev smoke). The suite asserts the submission passes
# L0-L9 (positive test) AND that tampered copies correctly FAIL (negatives).
# The verifier emits the COMMON result.json (schemas/result.schema.json) into
# /logs/verifier/ while retaining the L1-L9 scientific evidence.  reward.txt is
# written as a legacy compatibility fallback for older harnesses; the common
# harness reads result.json first.
#
# Staged layout inside the container:
#   /tests/test.sh  /tests/test_outputs.py  /tests/verifier.py
#   /tests/hidden/  (dft-validation.extxyz, rdf-reference.json, thresholds.json)
# Hidden paths are injected via task.toml [verifier.env]; verifier.py defaults
# to /tests/hidden/* when they are absent (dev smoke).
#
# Classification:
#   pytest passes  -> VALID_RESULT / PASS        (counted scientifically)
#   pytest fails   -> AGENT_FAILURE / SCIENTIFIC_FAIL
# ============================================================================
set -u
PY=/opt/ai2kit/bin/python
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
  "reason": "034 L1-L9 scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[034 test.sh] reward=1 result.json=VALID_RESULT"
  exit 0
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "AGENT_FAILURE",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "034 L1-L9 scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[034 test.sh] reward=0 result.json=AGENT_FAILURE"
  exit 1
fi
