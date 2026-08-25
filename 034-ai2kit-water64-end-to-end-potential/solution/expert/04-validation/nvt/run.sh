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
source "$(dirname "$0")/../../hpc-runtime.sh"
ai2kit_source_env

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
PLUGIN_LOAD="$(ai2kit_lammps_plugin_command)"
sed -e "s|@DP_MODELS@|$DP_MODELS|g" \
    -e "s|@PLUGIN_LOAD@|$PLUGIN_LOAD|g" \
    -e "s|@NVT_DUMP_FREQ@|$AI2KIT_NVT_DUMP_FREQ|g" \
    "$EXPERT_DIR/04-validation/nvt/nvt.in" > "$NVT_DIR/nvt.in"

# Deterministic NVT velocity seed (Task 5): derived from the root seed so the
# reference NVT is reproducible; never $RANDOM.
NVT_SEED="$(ai2kit_derived_seed "nvt")"

# --- slurm script -----------------------------------------------------------
cat > "$NVT_DIR/nvt.slurm" <<EOF
#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=nvt-300k
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=${AI2KIT_NVT_CPUS:-4}
#SBATCH --time=06:00:00
#SBATCH --output=${SBATCH_OUTPUT:-slurm.out}
#SBATCH --error=${SBATCH_ERROR:-slurm.out}
set -e
source "$EXPERT_DIR/hpc-runtime.sh"
ai2kit_source_env
cd "$NVT_DIR"
export OMP_NUM_THREADS="${AI2KIT_NVT_CPUS:-4}"
# ai2kit_lmp prepends the plugin-ABI LD_LIBRARY_PATH (libtensorflow_cc.so.2 +
# deepmd lib dir) and runs the pinned binary — same ABI fix as the AL sub-jobs.
ai2kit_lmp -i nvt.in -v restart 0 -v DATA_FILE "$DATA_FILE" -v TEMP 300 -v N_STEPS "$AI2KIT_NVT_STEPS" -v SEED $NVT_SEED
EOF
chmod +x "$NVT_DIR/nvt.slurm"

# --- submit + wait ----------------------------------------------------------
echo "[nvt] submitting NVT (steps=$AI2KIT_NVT_STEPS, models=$DP_MODELS, seed=$NVT_SEED)"
OUT="$(sbatch --parsable "$NVT_DIR/nvt.slurm")"
JOBID="$(echo "$OUT" | grep -oE '[0-9]+' | tail -n 1)"
echo "[nvt] job id: $JOBID"
# NVT is part of the reference calibration (Task 5): a job that does not
# complete cleanly is a FAILED calibration, never a warning.  wait_job.sh
# returns 0 only on COMPLETED; under `set -e` a failure aborts here before
# nvt.done is written.  The success marker therefore requires a clean NVT run
# AND successful analysis (output validation before the marker).
bash "$EXPERT_DIR/wait_job.sh" "$JOBID" 21600 "$NVT_DIR/nvt.slurm"

# --- analyze (required for nvt.done) ----------------------------------------
python "$EXPERT_DIR/04-validation/nvt/analyze_nvt.py" "$NVT_DIR"

touch "$NVT_DIR/nvt.done"
echo "[nvt] done -> $NVT_DIR"
