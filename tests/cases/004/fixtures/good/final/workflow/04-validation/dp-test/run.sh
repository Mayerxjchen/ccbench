#!/bin/bash
# ============================================================================
# 04-validation/dp-test — held-out E/F accuracy of the final committee.
#
# Input : $AI2KIT_CFG_DIR/aimd.xyz  (filtered mother set)
#         $AI2KIT_AL_DIR/iter-<last>/deepmd/model-*/compress.pb
# Output: $AI2KIT_VALIDATION_DIR/dp-test/test-data/O64H128   (DeepMD held-out)
#         $AI2KIT_VALIDATION_DIR/dp-test/output/model-*.{e_peratom,f}.out
#         $AI2KIT_VALIDATION_DIR/dp-test/output/dp-test.png
#
# Adapted from reference/expert-trajectory/validation/analysis/{run_dp_test.sh,
# prepare_test_data.py,dp-test.py}: env.sh replaces module/conda; dp test runs
# inline (light CPU inference, not scheduled). prepare_test_data.py holds out
# everything the setup sampling does NOT pick, so held-out is disjoint from the
# initial training frames.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../../env.sh"

DT_DIR="$AI2KIT_VALIDATION_DIR/dp-test"
TEST_DATA="$DT_DIR/test-data/O64H128"
RESULT_DIR="$DT_DIR/output"
AIMD_XYZ="$AI2KIT_CFG_DIR/aimd.xyz"
mkdir -p "$TEST_DATA" "$RESULT_DIR"

if [ ! -s "$AIMD_XYZ" ]; then
    echo "[dp-test] ERROR: no aimd.xyz at $AIMD_XYZ" >&2
    exit 1
fi

# --- locate final committee -------------------------------------------------
AL_DIR="$AI2KIT_AL_DIR"
LAST_ITER="$(ls -d "$AL_DIR"/iter-* 2>/dev/null | sort | tail -n 1 | xargs -n1 basename 2>/dev/null || echo iter-000)"
MODEL_DIR="$AL_DIR/$LAST_ITER/deepmd"
if [ ! -d "$MODEL_DIR" ]; then
    MODEL_DIR="$(ls -d "$AL_DIR"/iter-*/deepmd 2>/dev/null | sort | tail -n 1 || true)"
fi
NMODEL="$(ls "$MODEL_DIR"/model-*/compress.pb 2>/dev/null | wc -l | tr -d ' ')"
if [ "$NMODEL" -eq 0 ]; then
    echo "[dp-test] ERROR: no model-*/compress.pb under $MODEL_DIR" >&2
    exit 1
fi

# --- prepare held-out test data (disjoint from the setup train frames) ------
echo "[dp-test] preparing held-out test data (train-frames=$AI2KIT_SETUP_SAMPLE)"
python "$EXPERT_DIR/04-validation/dp-test/prepare_test_data.py" \
    --aimd "$AIMD_XYZ" \
    --output "$TEST_DATA" \
    --train-frames "$AI2KIT_SETUP_SAMPLE" \
    --type-map O H \
    --overwrite

# --- dp test per model ------------------------------------------------------
for model_pb in "$MODEL_DIR"/model-*/compress.pb; do
    [ -f "$model_pb" ] || continue
    mid="$(basename "$(dirname "$model_pb")")"   # model-0, model-1, ...
    result_prefix="$RESULT_DIR/$mid"
    echo "[dp-test] $mid <- $model_pb"
    dp test -m "$model_pb" -s "$TEST_DATA" -d "$result_prefix" -n 0
done

# --- metrics + parity plot --------------------------------------------------
echo "[dp-test] plotting + metrics"
python "$EXPERT_DIR/04-validation/dp-test/dp-test.py" \
    --result_prefix="$RESULT_DIR/model-*" \
    --output="$RESULT_DIR/dp-test.png"

# clean intermediate per-atom/volume files (keep e_peratom + f)
rm -f "$RESULT_DIR"/model-*.e.out "$RESULT_DIR"/model-*.v.out "$RESULT_DIR"/model-*.v_peratom.out

touch "$DT_DIR/dp-test.done"
echo "[dp-test] done -> $RESULT_DIR"
