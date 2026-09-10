#!/bin/bash
# Common verifier launcher: python -m bench.verifiers.launcher
# ============================================================================
# 032 verifier entry (harness contract: /tests/test.sh -> result.json).
#
# The graded submission must pass test_outputs.py (Curie-temperature scientific
# gates).  The verifier emits the COMMON result.json (schemas/result.schema.json)
# into /logs/verifier/ while reward.txt stays as a legacy compatibility
# fallback for older harnesses; the common harness reads result.json first.
#
# Classification:
#   pytest passes  -> VALID_RESULT / PASS        (counted scientifically)
#   pytest assertion failure -> VALID_RESULT / SCIENTIFIC_FAIL (counted result)
# ============================================================================
set -u
PY=/opt/matclaw/bin/python
if [ ! -x "$PY" ]; then
  # dev fallback: host python with ase/deepmd on PATH
  PY=python3
fi
mkdir -p /logs/verifier 2>/dev/null || true

cd /tests || exit 1
set +e
"$PY" -m pytest -q /tests/test_outputs.py -rA --junitxml=/tmp/bench-pytest.xml
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "PASS",
  "reason": "032 Curie-temperature scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[032 test.sh] reward=1 result.json=VALID_RESULT"
elif [ "$rc" -eq 1 ] && "$PY" -c '
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse("/tmp/bench-pytest.xml").getroot()
    suites = [root] if root.tag.endswith("testsuite") else root.findall(".//testsuite")
    tests = sum(int(s.attrib.get("tests", "0")) for s in suites)
    errors = sum(int(s.attrib.get("errors", "0")) for s in suites)
    failures = sum(int(s.attrib.get("failures", "0")) for s in suites)
    # A collected test assertion is a scientific verdict.  Collection,
    # import, setup, or empty-suite errors are infrastructure failures.
    sys.exit(0 if tests > 0 and errors == 0 and failures > 0 else 1)
except (OSError, ET.ParseError, TypeError, ValueError):
    sys.exit(1)
'; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "032 Curie-temperature scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[032 test.sh] reward=0 result.json=VALID_RESULT/SCIENTIFIC_FAIL"
  exit 1
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "INFRA_INVALID",
  "failure_code": "VERIFIER_FAILURE",
  "reason": "032 verifier test runner failed to collect or execute",
  "retryable": false,
  "is_counted_scientifically": false
}
JSON
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
