#!/bin/bash
# Case 032 entry point. Only public/ reaches /app; the protocol grid and the
# workflow live in /solution (never staged into the graded workspace).
set -euo pipefail
profile="${MATCLAW_PROFILE:-paper}"
output="${MATCLAW_OUTPUT:-/app}"
args=(--profile "$profile" --output "$output")
if [[ -n "${MATCLAW_SEED:-}" ]]; then
  args+=(--seed "$MATCLAW_SEED")
fi
exec /opt/matclaw/bin/python /solution/run_curie.py "${args[@]}"
