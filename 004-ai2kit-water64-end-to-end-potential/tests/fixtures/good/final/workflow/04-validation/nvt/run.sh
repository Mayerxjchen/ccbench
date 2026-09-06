#!/bin/bash
# ============================================================================
# 04-validation/nvt — LAMMPS NVT 300 K stability run with the final committee.
#
# Input : $AI2KIT_AL_DIR/lammps-data/000.data   starting structure
#         $AI2KIT_AL_DIR/iter-<last>/deepmd/model-*/compress.pb   final models
# Output: $AI2KIT_VALIDATION_DIR/nvt/{nvt.in,thermo.dat,model_devi.out,
#         dump.lammpstrj,nvt.done}
#
# Adapted from reference/expert-trajectory/validation/analysis/run_nvt.sh and
# config/nvt/{nvt.in,run.sh,slurm-header.sh}: module/conda replaced by env.sh;
# one container-scaled NVT run of AI2KIT_NVT_STEPS (smoke 200 / paper 5000).
# Heavy job -> pseudo-slurm (sbatch) + wait_job.sh.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../../env.sh"

NVT_DIR="$AI2KIT_VALIDATION_DIR/nvt"
mkdir -p "$NVT_DIR"

# --- locate final committee ------------------------------------------------
AL_DIR="$AI2KIT_AL_DIR"
LAST_ITER="$(ls -d "$AL_DIR"/iter-* 2>/dev/null | sort | tail -n 1 | xargs -n1 basename 2>/dev/null || echo iter-000)"
MODEL_DIR="$AL_DIR/$LAST_ITER/deepmd"
DP_MODELS="$(ls "$MODEL_DIR"/model-*/compress.pb 2>/dev/null | tr '\n' ' ')"
if [ -z "$DP_MODELS" ]; then
    # fall back to any iter
    MODEL_DIR="$(ls -d "$AL_DIR"/iter-*/deepmd 2>/dev/null | sort | tail -n 1 || true)"
    DP_MODELS="$(ls "$MODEL_DIR"/model-*/compress.pb 2>/dev/null | tr '\n' ' ' || true)"
fi
if [ -z "$DP_MODELS" ]; then
    echo "[nvt] ERROR: no model-*/compress.pb found under $AL_DIR" >&2
    exit 1
fi

DATA_FILE="$AL_DIR/lammps-data/000.data"
if [ ! -f "$DATA_FILE" ]; then
    DATA_FILE="$(ls "$AL_DIR"/lammps-data/*.data 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$DATA_FILE" ] || [ ! -f "$DATA_FILE" ]; then
    echo "[nvt] ERROR: no LAMMPS data file under $AL_DIR/lammps-data" >&2
    exit 1
fi

# --- inject models + plugin path + dump freq --------------------------------
PLUGIN="${DEEPMD_PLUGIN:-libdeepmd_lmp.so}"
sed -e "s|@DP_MODELS@|$DP_MODELS|g" \
    -e "s|@PLUGIN_PATH@|$PLUGIN|g" \
    -e "s|@NVT_DUMP_FREQ@|$AI2KIT_NVT_DUMP_FREQ|g" \
    "$EXPERT_DIR/04-validation/nvt/nvt.in" > "$NVT_DIR/nvt.in"

# --- slurm script -----------------------------------------------------------
cat > "$NVT_DIR/nvt.slurm" <<EOF
#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=nvt-300k
#SBATCH --partition=cpu
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=06:00:00
#SBATCH --output=slurm.out
#SBATCH --error=slurm.out
set -e
source "$EXPERT_DIR/env.sh"
cd "$NVT_DIR"
SEED=\$RANDOM
lmp -i nvt.in -v restart 0 -v DATA_FILE "$DATA_FILE" -v TEMP 300 -v N_STEPS "$AI2KIT_NVT_STEPS" -v SEED \$SEED
EOF
chmod +x "$NVT_DIR/nvt.slurm"

# --- submit + wait ----------------------------------------------------------
echo "[nvt] submitting NVT (steps=$AI2KIT_NVT_STEPS, models=$DP_MODELS)"
OUT="$(sbatch --parsable "$NVT_DIR/nvt.slurm")"
JOBID="$(echo "$OUT" | grep -oE '[0-9]+' | tail -n 1)"
echo "[nvt] job id: $JOBID"
bash "$EXPERT_DIR/wait_job.sh" "$JOBID" 21600 "$NVT_DIR/nvt.slurm"

# --- analyze (best-effort; smoke may have too few equil frames) -------------
set +e
python "$EXPERT_DIR/04-validation/nvt/analyze_nvt.py" "$NVT_DIR"
set -e

touch "$NVT_DIR/nvt.done"
echo "[nvt] done -> $NVT_DIR"
