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
  "reason": "033 domain-wall-search scientific gates passed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 1 > /logs/verifier/reward.txt
  echo "[033 test.sh] reward=1 result.json=VALID_RESULT"
  exit 0
elif [ "$rc" -eq 1 ] && "$PY" -c '
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse("/tmp/bench-pytest.xml").getroot()
    suites = [root] if root.tag.endswith("testsuite") else root.findall(".//testsuite")
    tests = sum(int(s.attrib.get("tests", "0")) for s in suites)
    errors = sum(int(s.attrib.get("errors", "0")) for s in suites)
    failures = sum(int(s.attrib.get("failures", "0")) for s in suites)
    sys.exit(0 if tests > 0 and errors == 0 and failures > 0 else 1)
except (OSError, ET.ParseError, TypeError, ValueError):
    sys.exit(1)
'; then
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "VALID_RESULT",
  "failure_code": "SCIENTIFIC_FAIL",
  "reason": "033 domain-wall-search scientific gates failed",
  "retryable": false,
  "is_counted_scientifically": true
}
JSON
  echo 0 > /logs/verifier/reward.txt
  echo "[033 test.sh] reward=0 result.json=VALID_RESULT/SCIENTIFIC_FAIL"
  exit 1
else
  cat > /logs/verifier/result.json <<'JSON'
{
  "run_id": "verifier",
  "result_class": "INFRA_INVALID",
  "failure_code": "VERIFIER_FAILURE",
  "reason": "033 verifier test runner failed to collect or execute",
  "retryable": false,
  "is_counted_scientifically": false
}
JSON
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
