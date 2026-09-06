#!/bin/bash
# ============================================================================
# run.sh — orchestrator for the 034 oracle expert workflow.
#
#   stage 01-geopt            CP2K GEO_OPT of the PACKMOL structure
#   stage 02-aimd             CP2K AIMD reference data (labeled mother set)
#   stage 03-active-learning  ai2-kit closed loop (train/explore/screen/label)
#   stage 04-validation       dp-test / NVT / RDF against the agent's own data
#   final/                    CONTRACT §4 output contract (manifest/models/
#                             workflow/provenance/validation/report.md)
#
# Every stage is idempotent (marker files); every heavy job goes through the
# pseudo-slurm (sbatch + wait_job.sh / omb --wait).
#
# Usage (container):
#   bash /path/to/solution/expert/run.sh          # paper profile (default)
#   AI2KIT_PROFILE=smoke bash .../run.sh          # harness proof
#
# Profile knobs live in solution/expert/env.sh (AI2KIT_PROFILE=smoke|paper).
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/hpc-runtime.sh"
ai2kit_source_env

WORKSPACE="$AI2KIT_034_WORKSPACE"
FINAL_DIR="$WORKSPACE/final"

echo "============================================================"
echo " 034 oracle expert workflow  (profile=$AI2KIT_PROFILE)"
echo " workspace=$WORKSPACE   input=$AI2KIT_034_INPUT_STRUCT"
echo "============================================================"

# ---------------------------------------------------------------- stages 01-04
echo "[run.sh] === stage 01: geopt ==="
bash "$EXPERT_DIR/01-geopt/run.sh"

echo "[run.sh] === stage 02: aimd ==="
bash "$EXPERT_DIR/02-aimd/run.sh"

echo "[run.sh] === stage 03: active-learning ==="
bash "$EXPERT_DIR/03-active-learning/run.sh"

echo "[run.sh] === stage 04: validation ==="
bash "$EXPERT_DIR/04-validation/run.sh"

# ---------------------------------------------------------------- final/ contract
echo "[run.sh] === building final/ ==="
AL_DIR="$AI2KIT_AL_DIR"
VAL_DIR="$AI2KIT_VALIDATION_DIR"

# locate the final committee (last completed iteration)
LAST_ITER="$(ls -d "$AL_DIR"/iter-* 2>/dev/null | sort | tail -n 1 | xargs -n1 basename 2>/dev/null || echo iter-000)"
MODEL_DIR="$AL_DIR/$LAST_ITER/deepmd"
if [ ! -d "$MODEL_DIR" ]; then
    MODEL_DIR="$(ls -d "$AL_DIR"/iter-*/deepmd 2>/dev/null | sort | tail -n 1 || true)"
fi
PRIMARY_MODEL="$(ls "$MODEL_DIR"/model-0/compress.pb 2>/dev/null || ls "$MODEL_DIR"/model-*/compress.pb 2>/dev/null | head -n 1 || true)"
if [ -z "$PRIMARY_MODEL" ] || [ ! -f "$PRIMARY_MODEL" ]; then
    echo "[run.sh] ERROR: no final compress.pb under $MODEL_DIR" >&2
    exit 1
fi
echo "[run.sh] final model: $PRIMARY_MODEL"

rm -rf "$FINAL_DIR"
mkdir -p "$FINAL_DIR/models/final" "$FINAL_DIR/provenance" "$FINAL_DIR/validation" "$FINAL_DIR/workflow"

# --- models -----------------------------------------------------------------
# canonical location expected by the CONTRACT §4 manifest example
cp "$PRIMARY_MODEL" "$FINAL_DIR/models/final/compress.pb"
# keep the whole committee under final/models/final for provenance
for mp in "$MODEL_DIR"/model-*/compress.pb; do
    [ -f "$mp" ] || continue
    mid="$(basename "$(dirname "$mp")")"
    cp "$mp" "$FINAL_DIR/models/final/$mid-compress.pb"
done
# belt-and-suspenders: also make the manifest path resolve at the submission
# root (some verifier builds join model_files against the workspace root).
mkdir -p "$WORKSPACE/models/final"
cp "$PRIMARY_MODEL" "$WORKSPACE/models/final/compress.pb"

# --- workflow (self-contained copy of the 4 stages + top-level scripts) -----
cp -r "$EXPERT_DIR/01-geopt"          "$FINAL_DIR/workflow/"
cp -r "$EXPERT_DIR/02-aimd"           "$FINAL_DIR/workflow/"
cp -r "$EXPERT_DIR/03-active-learning" "$FINAL_DIR/workflow/"
cp -r "$EXPERT_DIR/04-validation"     "$FINAL_DIR/workflow/"
cp "$EXPERT_DIR/run.sh" "$EXPERT_DIR/env.sh" "$EXPERT_DIR/env-hpc.sh" \
   "$EXPERT_DIR/hpc-runtime.sh" "$EXPERT_DIR/wait_job.sh" \
   "$EXPERT_DIR/manifest_writer.py" "$FINAL_DIR/workflow/"
# drop any stale markers/logs that got copied from the live run
find "$FINAL_DIR/workflow" -name '*.done' -delete 2>/dev/null || true
find "$FINAL_DIR/workflow" -name 'slurm.out' -delete 2>/dev/null || true

# --- validation artifacts ---------------------------------------------------
cp -r "$VAL_DIR/." "$FINAL_DIR/validation/"

# --- provenance -------------------------------------------------------------
cat > "$FINAL_DIR/provenance/provenance.json" <<EOF
{
  "initial_structure": "$AI2KIT_034_INPUT_STRUCT",
  "geopt_output": "$AI2KIT_GEOPT_DIR/output/water64_geopt-pos-1.xyz",
  "aimd_raw": "$AI2KIT_AIMD_DIR/output/water64_aimd-{pos-1,frc-1}.xyz, water64_aimd-1.{cell,ener}",
  "aimd_mother_set": "$AI2KIT_CFG_DIR/aimd.xyz",
  "active_learning_rounds": "$AL_DIR/iter-*/",
  "final_committee": "$MODEL_DIR",
  "profile": "$AI2KIT_PROFILE"
}
EOF
# first-principles reference data (real CP2K AIMD outputs) inside the contract
mkdir -p "$FINAL_DIR/provenance/aimd-raw"
cp "$AI2KIT_AIMD_DIR"/output/water64_aimd-pos-1.xyz \
   "$AI2KIT_AIMD_DIR"/output/water64_aimd-frc-1.xyz \
   "$AI2KIT_AIMD_DIR"/output/water64_aimd-1.cell \
   "$AI2KIT_AIMD_DIR"/output/water64_aimd-1.ener \
   "$FINAL_DIR/provenance/aimd-raw/" 2>/dev/null || true
cp "$AI2KIT_CFG_DIR/aimd.xyz" "$FINAL_DIR/provenance/aimd.xyz" 2>/dev/null || true

# --- manifest + report (from real artifacts) --------------------------------
python "$EXPERT_DIR/manifest_writer.py" "$FINAL_DIR" "$WORKSPACE"

echo "============================================================"
echo " final/ written under $FINAL_DIR"
echo "   manifest.json  models/  workflow/  provenance/  validation/  report.md"
echo "============================================================"
