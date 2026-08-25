#!/bin/bash
# ============================================================================
# 04-validation — agent-side scientific validation of the final committee:
#   dp-test (held-out E/F)  ->  NVT 300 K stability  ->  RDF (AIMD vs MLP)
# Each sub-stage is idempotent; artifacts land under $AI2KIT_VALIDATION_DIR.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../env.sh"

VALIDATION_DIR="$AI2KIT_VALIDATION_DIR"
mkdir -p "$VALIDATION_DIR"

if [ -f "$VALIDATION_DIR/validation.done" ]; then
    echo "[04-validation] already done -> skip"
    exit 0
fi

# --- prerequisites ----------------------------------------------------------
if [ ! -f "$AI2KIT_AL_DIR/al.done" ]; then
    echo "[04-validation] ERROR: 03-active-learning not complete" >&2
    exit 1
fi

echo "[04-validation] === dp-test ==="
bash "$EXPERT_DIR/04-validation/dp-test/run.sh"

echo "[04-validation] === nvt ==="
bash "$EXPERT_DIR/04-validation/nvt/run.sh"

echo "[04-validation] === rdf ==="
bash "$EXPERT_DIR/04-validation/rdf/run.sh"

touch "$VALIDATION_DIR/validation.done"
echo "[04-validation] done -> $VALIDATION_DIR"
