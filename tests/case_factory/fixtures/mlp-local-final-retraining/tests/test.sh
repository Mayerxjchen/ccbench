#!/usr/bin/env bash
# Real Verifier entry for the local Final-Retraining fixture.
#
# Mounted read-only at /submission (the sealed candidate submission) and
# writable at /logs/verifier.  Writes a result.schema.json-conforming
# result.json: PASS when the sealed final output equals the declared bytes,
# SCIENTIFIC_FAIL otherwise.  /tests, /submission and /logs/verifier are all
# the container's concern; nothing here touches the host.
set -euo pipefail

OUT=/logs/verifier/result.json
RESULT_FILE=/submission/result.txt

CODE=SCIENTIFIC_FAIL
REASON="sealed submission missing final/result.txt"
if [ -f "$RESULT_FILE" ]; then
  CONTENT="$(cat "$RESULT_FILE")"
  if [ "$CONTENT" = "final accuracy 0.9876" ]; then
    CODE=PASS
    REASON="declared final output present"
  else
    REASON="unexpected final output: ${CONTENT}"
  fi
fi

cat > "$OUT" <<EOF
{"run_id":"placeholder","result_class":"VALID_RESULT","failure_code":"$CODE","reason":"$REASON","retryable":false}
EOF
