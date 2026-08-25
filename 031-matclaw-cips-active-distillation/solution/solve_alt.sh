#!/bin/bash
# Alternative-path entry point for Case 031 (independent solver).
# Runs the same artifact contract through alt_distillation.py so the hidden
# verifier can grade an independently-written implementation.
set -euo pipefail
profile="${MATCLAW_PROFILE:?MATCLAW_PROFILE is required}"
seed="${MATCLAW_SEED:?MATCLAW_SEED is required}"
output="${MATCLAW_OUTPUT:-/app}"
exec /opt/matclaw/bin/python /solution/alt_distillation.py \
  --profile "$profile" --seed "$seed" --output "$output"
