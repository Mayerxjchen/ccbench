#!/bin/bash
# Common verifier launcher: python -m ccbench.verifiers.launcher

# ============================================================================
# 031 verifier entry (harness contract: /tests/test.sh -> result.json).
#
# The graded submission must pass the hidden verifier's active-distillation
# scientific gates.  The independent verifier recomputes held-out forces via
# deepmd DP forward passes, replays every active-transition selection, and
# audits dataset growth/checkpoint hashes (tests/verifier.py::verify).  This
# entry calls verify() DIRECTLY — the same gate the formal finalize path runs
# on HPC (scripts/evidence/finalize_run.py) — instead of running the
# development pytest suites (test_outputs.py / test_active_contract.py are
# defense-in-depth and retrain/rewrite fixtures that do not belong in the
# read-only verifier container).  The verifier emits the COMMON result.json
# (schemas/result.schema.json) into /logs/verifier/ while reward.txt stays as
# a legacy compatibility fallback; the common harness reads result.json first.
#
# Classification:
#   verify() valid   -> VALID_RESULT / PASS        (counted scientifically)
#   verify() invalid -> AGENT_FAILURE / SCIENTIFIC_FAIL
# ============================================================================
set -u
PY=/opt/matclaw/bin/python
if [ ! -x "$PY" ]; then
  # dev fallback: host python with ase/deepmd on PATH
  PY=python3
fi
mkdir -p /logs/verifier 2>/dev/null || true

cd /tests || exit 1
if "$PY" -c '
import sys
sys.path.insert(0, "/tests")
from pathlib import Path
from verifier import verify
report = verify(Path("/app"), "paper")
print("valid=", report["valid"])
for error in report.get("errors", []):
    print("  error:", error)
sys.exit(0 if report["valid"] else 1)
'; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "PASS",
  "reason": "031 active-distillation scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[031 test.sh] reward=1 result.json=VALID_RESULT"
  exit 0
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "AGENT_FAILURE",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "031 active-distillation scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[031 test.sh] reward=0 result.json=AGENT_FAILURE"
  exit 1
fi
