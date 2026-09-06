#!/bin/bash
# Case 033 alternative solver (G11): independent smoke-only implementation.
set -euo pipefail
profile="${MATCLAW_PROFILE:-smoke}"
seed="${MATCLAW_SEED:?MATCLAW_SEED is required}"
output="${MATCLAW_OUTPUT:-/app}"
exec /opt/matclaw/bin/python /solution/alt_search.py \
  --profile "$profile" --seed "$seed" --output "$output"
