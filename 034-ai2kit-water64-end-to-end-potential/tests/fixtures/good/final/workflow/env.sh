#!/bin/bash
# ============================================================================
# env.sh — container environment for the 034 oracle expert workflow.
#
# Sourced by every script in solution/expert AND by every job script that the
# workflow generates (geopt / aimd / deepmd train / lammps explore / cp2k label /
# nvt / dp-test).  It replaces the HPC `module load` / `conda activate <hpc>`
# boilerplate of the archived expert trajectory.
#
# Container facts (dftworld-base-ai2kit, v1 CPU):
#   - python3.11 venv at /opt/ai2kit, /opt/ai2kit/bin on PATH
#   - cp2k.psmp / cp2k.popt (FROM cp2k/cp2k:latest, provides GTH basis/potential files)
#   - lmp (deepmd-kit[lmp]) + LAMMPS DeePMD plugin (libdeepmd_lmp.so)
#   - dp (deepmd-kit 2.2.11), ai2-kit 1.1.0, omb (oh-my-batch 0.7.6)
#   - pseudo-slurm on PATH (sbatch / squeue / sacct / scancel / sinfo)
#   - a GPU is *requestable* via --gres=gpu:1; the v1 image is CPU-only.
#
# Profile contract:
#   AI2KIT_PROFILE = paper (default, formal) | smoke (harness proof)
#   Setting AI2KIT_SMOKE=1 is an alias for AI2KIT_PROFILE=smoke.
#   Every schedule parameter can be individually overridden through the
#   environment (e.g. AI2KIT_AIMD_STEPS=10000 for a fuller AIMD run).
# ============================================================================

# --- location of this tree ------------------------------------------------
_EXPERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export EXPERT_DIR="$_EXPERT_DIR"
export CASE_ROOT="$(cd "$_EXPERT_DIR/../.." && pwd)"

# --- python venv -----------------------------------------------------------
export AI2KIT_VENV="${AI2KIT_VENV:-/opt/ai2kit}"
if [ -d "$AI2KIT_VENV/bin" ]; then
  export PATH="$AI2KIT_VENV/bin:$PATH"
fi

# --- DeePMD LAMMPS plugin (locate defensively) -----------------------------
# deepmd-kit[lmp] installs libdeepmd_lmp.so inside the venv.  LAMMPS needs
# LAMMPS_PLUGIN_PATH to find it, and `plugin load libdeepmd_lmp.so` (or an
# absolute path) to expose `pair_style deepmd`.
if [ -n "${LAMMPS_PLUGIN_PATH:-}" ] && [ -f "$LAMMPS_PLUGIN_PATH/libdeepmd_lmp.so" ]; then
  DEEPMD_PLUGIN="$LAMMPS_PLUGIN_PATH/libdeepmd_lmp.so"
else
  _DEEPMD_LIB="$(find "$AI2KIT_VENV" -name 'libdeepmd_lmp.so' 2>/dev/null | head -n 1)"
  if [ -n "$_DEEPMD_LIB" ]; then
    export LAMMPS_PLUGIN_PATH="$(dirname "$_DEEPMD_LIB")"
    DEEPMD_PLUGIN="$_DEEPMD_LIB"
  else
    DEEPMD_PLUGIN=""
  fi
  unset _DEEPMD_LIB
fi
export DEEPMD_PLUGIN

# shared-library path for the plugin + its deps (libtensorflow_cc etc.)
if [ -n "${LAMMPS_PLUGIN_PATH:-}" ]; then
  export LD_LIBRARY_PATH="$LAMMPS_PLUGIN_PATH:${LD_LIBRARY_PATH:-}"
fi
export LD_LIBRARY_PATH="$AI2KIT_VENV/lib:${LD_LIBRARY_PATH:-}"

# --- TF / OpenMP -----------------------------------------------------------
export TF_CPP_MIN_LOG_LEVEL="${TF_CPP_MIN_LOG_LEVEL:-3}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export TF_INTER_OP_PARALLELISM_THREADS="${TF_INTER_OP_PARALLELISM_THREADS:-1}"
export TF_INTRA_OP_PARALLELISM_THREADS="${TF_INTRA_OP_PARALLELISM_THREADS:-${OMP_NUM_THREADS:-1}}"
export DP_INTER_OP_PARALLELISM_THREADS="${DP_INTER_OP_PARALLELISM_THREADS:-1}"
export DP_INTRA_OP_PARALLELISM_THREADS="${DP_INTRA_OP_PARALLELISM_THREADS:-${OMP_NUM_THREADS:-1}}"

# --- workspace / public inputs --------------------------------------------
# In the container the public inputs are copied to /app; the oracle writes its
# final/ contract into $AI2KIT_034_WORKSPACE/final (default /app/final).
if [ -d /app ]; then
  export AI2KIT_034_WORKSPACE="${AI2KIT_034_WORKSPACE:-/app}"
else
  export AI2KIT_034_WORKSPACE="${AI2KIT_034_WORKSPACE:-$CASE_ROOT}"
fi
if [ -f /app/water64.xyz ]; then
  export AI2KIT_034_INPUT_STRUCT="${AI2KIT_034_INPUT_STRUCT:-/app/water64.xyz}"
else
  export AI2KIT_034_INPUT_STRUCT="${AI2KIT_034_INPUT_STRUCT:-$CASE_ROOT/public/water64.xyz}"
fi

# --- profile ----------------------------------------------------------------
export AI2KIT_PROFILE="${AI2KIT_PROFILE:-paper}"
if [ "$AI2KIT_PROFILE" = "smoke" ] || [ "${AI2KIT_SMOKE:-0}" = "1" ]; then
  export AI2KIT_SMOKE=1
  export AI2KIT_PROFILE="smoke"
else
  export AI2KIT_SMOKE=0
fi

# --- schedule ---------------------------------------------------------------
# smoke: tiny everything, completes in tens of minutes (harness proof).
# paper : full expert schedule scaled to what a 16-core CPU container can
#         finish (AIMD ~2000 steps, training ~40k steps, 1-3 AL rounds).
if [ "$AI2KIT_SMOKE" = "1" ]; then
  export AI2KIT_GEO_OPT_MAX_ITER="${AI2KIT_GEO_OPT_MAX_ITER:-30}"
  export AI2KIT_AIMD_STEPS="${AI2KIT_AIMD_STEPS:-100}"
  export AI2KIT_AIMD_TRAJ_EACH="${AI2KIT_AIMD_TRAJ_EACH:-5}"
  export AI2KIT_AIMD_WALLTIME="${AI2KIT_AIMD_WALLTIME:-7200}"
  export AI2KIT_AIMD_MIN_TEMP="${AI2KIT_AIMD_MIN_TEMP:-200}"
  export AI2KIT_AIMD_MAX_TEMP="${AI2KIT_AIMD_MAX_TEMP:-400}"
  export AI2KIT_TRAIN_STEPS="${AI2KIT_TRAIN_STEPS:-200}"
  export AI2KIT_DECAY_STEPS="${AI2KIT_DECAY_STEPS:-50}"
  export AI2KIT_SAVE_FREQ="${AI2KIT_SAVE_FREQ:-50}"
  export AI2KIT_DISP_FREQ="${AI2KIT_DISP_FREQ:-20}"
  export AI2KIT_NUMB_TEST="${AI2KIT_NUMB_TEST:-2}"
  export AI2KIT_MODEL_NUM="${AI2KIT_MODEL_NUM:-2}"
  export AI2KIT_AL_ROUNDS="${AI2KIT_AL_ROUNDS:-1}"
  export AI2KIT_MD_STEPS="${AI2KIT_MD_STEPS:-100}"
  export AI2KIT_MD_TEMP="${AI2KIT_MD_TEMP:-330 430}"
  export AI2KIT_SAMPLE_FREQ="${AI2KIT_SAMPLE_FREQ:-5}"
  export AI2KIT_MAX_LABEL="${AI2KIT_MAX_LABEL:-3}"
  # smoke uses a very wide "decent" band so a barely-trained 2-model committee
  # is guaranteed to produce >=1 labeled configuration (harness-proof loop).
  export AI2KIT_MODEL_DEVI_COND="${AI2KIT_MODEL_DEVI_COND:---lo 0.001 --hi 5.0}"
  export AI2KIT_DEVI_SLICE="${AI2KIT_DEVI_SLICE:-0:}"
  export AI2KIT_SETUP_SAMPLE="${AI2KIT_SETUP_SAMPLE:-8}"
  export AI2KIT_LAMMPS_SAMPLE="${AI2KIT_LAMMPS_SAMPLE:-2}"
  export AI2KIT_NVT_STEPS="${AI2KIT_NVT_STEPS:-200}"
  export AI2KIT_NVT_DUMP_FREQ="${AI2KIT_NVT_DUMP_FREQ:-50}"
  export AI2KIT_RDF_SKIP="${AI2KIT_RDF_SKIP:-1}"
else
  export AI2KIT_GEO_OPT_MAX_ITER="${AI2KIT_GEO_OPT_MAX_ITER:-200}"
  export AI2KIT_AIMD_STEPS="${AI2KIT_AIMD_STEPS:-5000}"
  export AI2KIT_AIMD_TRAJ_EACH="${AI2KIT_AIMD_TRAJ_EACH:-25}"
  export AI2KIT_AIMD_WALLTIME="${AI2KIT_AIMD_WALLTIME:-80000}"
  export AI2KIT_AIMD_MIN_TEMP="${AI2KIT_AIMD_MIN_TEMP:-250}"
  export AI2KIT_AIMD_MAX_TEMP="${AI2KIT_AIMD_MAX_TEMP:-350}"
  export AI2KIT_TRAIN_STEPS="${AI2KIT_TRAIN_STEPS:-40000}"
  export AI2KIT_DECAY_STEPS="${AI2KIT_DECAY_STEPS:-1000}"
  export AI2KIT_SAVE_FREQ="${AI2KIT_SAVE_FREQ:-1000}"
  export AI2KIT_DISP_FREQ="${AI2KIT_DISP_FREQ:-100}"
  export AI2KIT_NUMB_TEST="${AI2KIT_NUMB_TEST:-10}"
  export AI2KIT_MODEL_NUM="${AI2KIT_MODEL_NUM:-4}"
  export AI2KIT_AL_ROUNDS="${AI2KIT_AL_ROUNDS:-2}"
  export AI2KIT_MD_STEPS="${AI2KIT_MD_STEPS:-4000}"
  export AI2KIT_MD_TEMP="${AI2KIT_MD_TEMP:-330 430 530}"
  export AI2KIT_SAMPLE_FREQ="${AI2KIT_SAMPLE_FREQ:-100}"
  export AI2KIT_MAX_LABEL="${AI2KIT_MAX_LABEL:-8}"
  export AI2KIT_MODEL_DEVI_COND="${AI2KIT_MODEL_DEVI_COND:---lo 0.2 --hi 0.4}"
  export AI2KIT_DEVI_SLICE="${AI2KIT_DEVI_SLICE:-10:}"
  export AI2KIT_SETUP_SAMPLE="${AI2KIT_SETUP_SAMPLE:-50}"
  export AI2KIT_LAMMPS_SAMPLE="${AI2KIT_LAMMPS_SAMPLE:-2}"
  export AI2KIT_NVT_STEPS="${AI2KIT_NVT_STEPS:-5000}"
  export AI2KIT_NVT_DUMP_FREQ="${AI2KIT_NVT_DUMP_FREQ:-100}"
  export AI2KIT_RDF_SKIP="${AI2KIT_RDF_SKIP:-20}"
fi

# --- derived workspace paths ----------------------------------------------
export AI2KIT_WORK_DIR="$AI2KIT_034_WORKSPACE/work"
export AI2KIT_CFG_DIR="$AI2KIT_WORK_DIR/config"
export AI2KIT_GEOPT_DIR="$AI2KIT_WORK_DIR/geopt"
export AI2KIT_AIMD_DIR="$AI2KIT_WORK_DIR/aimd"
export AI2KIT_AL_DIR="$AI2KIT_WORK_DIR/al"
export AI2KIT_VALIDATION_DIR="$AI2KIT_WORK_DIR/validation"

echo "[env.sh] profile=$AI2KIT_PROFILE workspace=$AI2KIT_034_WORKSPACE"
echo "[env.sh] input=$AI2KIT_034_INPUT_STRUCT"
echo "[env.sh] deepmd_plugin=${DEEPMD_PLUGIN:-<NOT-FOUND>}"
