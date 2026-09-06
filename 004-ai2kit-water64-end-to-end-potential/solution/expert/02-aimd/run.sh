#!/bin/bash
# ============================================================================
# 02-aimd — CP2K AIMD (NVT 300 K) reference-data generation + extxyz conversion.
#
# Input : $AI2KIT_GEOPT_DIR/output/water64_geopt-pos-1.xyz (optimised structure)
# Output: $AI2KIT_AIMD_DIR/output/water64_aimd-{pos-1,frc-1}.xyz,
#         water64_aimd-1.{cell,ener}                        (raw CP2K outputs)
#         $AI2KIT_CFG_DIR/aimd.xyz                          (labeled mother set)
#         $AI2KIT_AIMD_DIR/processed/filter.tsv             (frame filter report)
#         $AI2KIT_AIMD_DIR/processed/provenance.json
#         $AI2KIT_AIMD_DIR/aimd.done                        (marker)
#
# Adapted from reference/expert-trajectory/aimd/:
#   - starting coord_n_cell.inc is generated from the OPTIMISED geopt output
#     (last frame) by tools/xyz2cp2k_inc.py
#   - STEPS / trajectory-print cadence / WALLTIME are env-gated
#   - conversion uses 02-aimd/convert.py, a self-contained port of the archived
#     tools/cp2k2extxyz.py (drop step 0, dedupe, temperature window, no padding)
# Idempotent: skips when aimd.done exists.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../hpc-runtime.sh"
ai2kit_source_env

AIMD_DIR="$AI2KIT_AIMD_DIR"
CFG_DIR="$AI2KIT_CFG_DIR"
INPUT="$AIMD_DIR/input"
OUTPUT="$AIMD_DIR/output"
PROC="$AIMD_DIR/processed"

if [ -f "$AIMD_DIR/aimd.done" ]; then
  echo "[02-aimd] already done -> skip"
  exit 0
fi

# --- 0. geopt prerequisite -------------------------------------------------
if [ ! -f "$AI2KIT_GEOPT_DIR/geopt.done" ] || [ ! -s "$AI2KIT_GEOPT_DIR/output/water64_geopt-pos-1.xyz" ]; then
  echo "[02-aimd] ERROR: geopt not complete" >&2
  exit 1
fi

mkdir -p "$INPUT" "$OUTPUT" "$PROC" "$CFG_DIR"

# --- 1. coord include from the OPTIMISED structure (last geopt frame) ------
echo "[02-aimd] coord_n_cell.inc from optimised geopt structure"
python "$EXPERT_DIR/tools/xyz2cp2k_inc.py" \
  "$AI2KIT_GEOPT_DIR/output/water64_geopt-pos-1.xyz" \
  "$INPUT/coord_n_cell.inc" \
  --cell 12.4 \
  --frame last

# --- 2. aimd.inp template -> concrete input --------------------------------
echo "[02-aimd] writing aimd.inp (steps=$AI2KIT_AIMD_STEPS, each=$AI2KIT_AIMD_TRAJ_EACH)"
sed -e "s|@AIMD_STEPS@|$AI2KIT_AIMD_STEPS|g" \
  -e "s|@AIMD_TRAJ_EACH@|$AI2KIT_AIMD_TRAJ_EACH|g" \
  -e "s|@WALLTIME@|$AI2KIT_AIMD_WALLTIME|g" \
  "$EXPERT_DIR/02-aimd/aimd.inp" > "$INPUT/aimd.inp"

# --- 3. slurm script --------------------------------------------------------
CP2K_NP="${AI2KIT_CP2K_NP:-4}"
cat > "$AIMD_DIR/aimd.slurm" <<EOF
#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=water-aimd
#SBATCH --ntasks=$CP2K_NP
#SBATCH --cpus-per-task=${AI2KIT_CP2K_OMP:-1}
#SBATCH --time=24:00:00
#SBATCH --output=${SBATCH_OUTPUT:-slurm.out}
#SBATCH --error=${SBATCH_ERROR:-slurm.out}
set -e
source "$EXPERT_DIR/hpc-runtime.sh"
ai2kit_source_env
cd "$OUTPUT"
ln -sf ../input/coord_n_cell.inc .
cp ../input/aimd.inp .
ai2kit_run_cp2k $CP2K_NP aimd.inp output
EOF
chmod +x "$AIMD_DIR/aimd.slurm"

# --- 4. submit + wait -------------------------------------------------------
echo "[02-aimd] submitting AIMD job (steps=$AI2KIT_AIMD_STEPS)"
OUT="$(sbatch --parsable "$AIMD_DIR/aimd.slurm")"
JOBID="$(echo "$OUT" | grep -oE '[0-9]+' | tail -n 1)"
echo "[02-aimd] job id: $JOBID"
bash "$EXPERT_DIR/wait_job.sh" "$JOBID" 86400 "$AIMD_DIR/aimd.slurm"

# --- 5. verify raw outputs --------------------------------------------------
for f in water64_aimd-pos-1.xyz water64_aimd-frc-1.xyz water64_aimd-1.cell water64_aimd-1.ener; do
  if [ ! -s "$OUTPUT/$f" ]; then
    echo "[02-aimd] ERROR: missing/non-empty $f" >&2
    tail -n 30 "$OUTPUT/output" 2>/dev/null || true
    exit 1
  fi
done

# --- 6. convert raw CP2K outputs -> labeled extxyz mother set ---------------
echo "[02-aimd] converting raw outputs to labeled extxyz"
python "$EXPERT_DIR/02-aimd/convert.py" \
  --dir "$OUTPUT" \
  --prefix water64_aimd \
  --min-temp "$AI2KIT_AIMD_MIN_TEMP" \
  --max-temp "$AI2KIT_AIMD_MAX_TEMP" \
  --output "$CFG_DIR/aimd.xyz" \
  --report "$PROC/filter.tsv"

# --- 7. provenance ----------------------------------------------------------
python - "$PROC" "$CFG_DIR/aimd.xyz" <<'PYEOF'
import json, os, sys
proc_dir, xyz = sys.argv[1], sys.argv[2]
# count raw frames from the pos trajectory
raw = 0
with open(os.path.join(proc_dir, os.pardir, "output", "water64_aimd-pos-1.xyz")) as f:
    for line in f:
        line = line.strip()
        if line and line.split()[0].isdigit():
            raw += 1
n_frame = 0
with open(xyz) as f:
    for line in f:
        line = line.strip()
        if line and line.split()[0].isdigit():
            n_frame += 1
prov = {
    "source": "CP2K AIMD (NVT 300 K, BLYP-D3/TZV2P-GTH) from optimised 64-H2O structure",
    "raw_frames": raw,
    "filtered_frames": n_frame,
    "temperature_window_K": [float(os.environ.get("AI2KIT_AIMD_MIN_TEMP", 250)),
                             float(os.environ.get("AI2KIT_AIMD_MAX_TEMP", 350))],
    "filter_rule": "drop step 0 + drop duplicate steps + temperature window; no padding",
    "output": os.path.join(proc_dir, "..", "..", "config", "aimd.xyz"),
}
with open(os.path.join(proc_dir, "provenance.json"), "w") as f:
    json.dump(prov, f, indent=2)
print(json.dumps(prov, indent=2))
PYEOF

touch "$AIMD_DIR/aimd.done"
echo "[02-aimd] done -> $CFG_DIR/aimd.xyz"
