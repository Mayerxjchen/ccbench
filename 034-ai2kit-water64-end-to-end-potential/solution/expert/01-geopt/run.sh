#!/bin/bash
# ============================================================================
# 01-geopt — CP2K GEO_OPT of the initial PACKMOL 64-H2O structure.
#
# Input : $AI2KIT_034_INPUT_STRUCT (caller-provided: PAgent-generated or hidden reference PACKMOL fill)
# Output: $AI2KIT_GEOPT_DIR/output/water64_geopt-pos-1.xyz  (optimised trajectory)
#         $AI2KIT_GEOPT_DIR/geopt.done                     (marker)
#
# Adapted from reference/expert-trajectory/geopt/:
#   - `@include coord_n_cell.inc` is relative (no HPC absolute @include path)
#   - coord_n_cell.inc is generated from the supplied structure by
#     tools/xyz2cp2k_inc.py (self-contained port of the reference tool)
#   - MAX_ITER is env-gated (@MAX_ITER@ -> $AI2KIT_GEO_OPT_MAX_ITER)
#   - submitted through the pseudo-slurm contract and awaited via wait_job.sh
# Idempotent: skips straight to the end when geopt.done exists.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../hpc-runtime.sh"
ai2kit_source_env

GEOPT_DIR="$AI2KIT_GEOPT_DIR"
INPUT="$GEOPT_DIR/input"
OUTPUT="$GEOPT_DIR/output"

if [ -f "$GEOPT_DIR/geopt.done" ]; then
  echo "[01-geopt] already done -> skip"
  exit 0
fi

mkdir -p "$INPUT" "$OUTPUT"

# --- 1. structure -> CP2K include file ------------------------------------
echo "[01-geopt] generating coord_n_cell.inc from $AI2KIT_034_INPUT_STRUCT"
python "$EXPERT_DIR/tools/xyz2cp2k_inc.py" \
  "$AI2KIT_034_INPUT_STRUCT" \
  "$INPUT/coord_n_cell.inc" \
  --cell 12.4 \
  --frame 0

# --- 2. geopt.inp template -> concrete input ------------------------------
echo "[01-geopt] writing geopt.inp (MAX_ITER=$AI2KIT_GEO_OPT_MAX_ITER)"
sed -e "s|@MAX_ITER@|$AI2KIT_GEO_OPT_MAX_ITER|g" \
  "$EXPERT_DIR/01-geopt/geopt.inp" > "$INPUT/geopt.inp"

# --- 3. slurm script -------------------------------------------------------
CP2K_NP="${AI2KIT_CP2K_NP:-4}"
cat > "$GEOPT_DIR/geopt.slurm" <<EOF
#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=water-geopt
#SBATCH --ntasks=$CP2K_NP
#SBATCH --cpus-per-task=${AI2KIT_CP2K_OMP:-1}
#SBATCH --time=06:00:00
#SBATCH --output=${SBATCH_OUTPUT:-slurm.out}
#SBATCH --error=${SBATCH_ERROR:-slurm.out}
set -e
source "$EXPERT_DIR/hpc-runtime.sh"
ai2kit_source_env
cd "$OUTPUT"
ln -sf ../input/coord_n_cell.inc .
cp ../input/geopt.inp .
ai2kit_run_cp2k $CP2K_NP geopt.inp output
EOF
chmod +x "$GEOPT_DIR/geopt.slurm"

# --- 4. submit + wait ------------------------------------------------------
echo "[01-geopt] submitting geopt job"
OUT="$(sbatch --parsable "$GEOPT_DIR/geopt.slurm")"
JOBID="$(echo "$OUT" | grep -oE '[0-9]+' | tail -n 1)"
echo "[01-geopt] job id: $JOBID"
bash "$EXPERT_DIR/wait_job.sh" "$JOBID" 21600 "$GEOPT_DIR/geopt.slurm"

# --- 5. sanity + harvest ---------------------------------------------------
if [ ! -s "$OUTPUT/water64_geopt-pos-1.xyz" ]; then
  echo "[01-geopt] ERROR: no geopt trajectory produced" >&2
  tail -n 30 "$OUTPUT/output" 2>/dev/null || true
  exit 1
fi

touch "$GEOPT_DIR/geopt.done"
echo "[01-geopt] done -> $OUTPUT/water64_geopt-pos-1.xyz"
