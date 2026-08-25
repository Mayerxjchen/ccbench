#!/bin/bash
set -euo pipefail
profile="${MATCLAW_PROFILE:?MATCLAW_PROFILE is required}"
seed="${MATCLAW_SEED:?MATCLAW_SEED is required}"
output="${MATCLAW_OUTPUT:-/app}"
exec /opt/matclaw/bin/python /solution/run_search.py \
  --profile "$profile" --seed "$seed" --output "$output"
