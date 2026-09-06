#!/bin/bash
# Case 031 entry point. Only public/ reaches /app; the workflow and the hidden
# exploration profile live in /solution (never staged into the graded workspace).
set -euo pipefail
args=(--profile "${MATCLAW_PROFILE:?required}" --seed "${MATCLAW_SEED:?required}" \
      --output "${MATCLAW_OUTPUT:-/app}")
if [ "${MATCLAW_RESUME:-0}" = "1" ]; then args+=(--resume); fi
exec /opt/matclaw/bin/python /solution/run_distillation.py "${args[@]}"
