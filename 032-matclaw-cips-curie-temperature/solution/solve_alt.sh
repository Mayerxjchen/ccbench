#!/bin/bash
# Case 032 alternative solver entry point (G11 alternative-valid evidence).
set -euo pipefail
profile="${MATCLAW_PROFILE:-smoke}"
output="${MATCLAW_OUTPUT:-/app}"
args=(--profile "$profile" --output "$output")
if [[ -n "${MATCLAW_SEED:-}" ]]; then
  args+=(--seed "$MATCLAW_SEED")
fi
exec /opt/matclaw/bin/python /solution/alt_curie.py "${args[@]}"
