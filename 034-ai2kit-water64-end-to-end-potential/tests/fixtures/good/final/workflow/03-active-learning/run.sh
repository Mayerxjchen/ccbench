#!/bin/bash
# ============================================================================
# 03-active-learning — ai2-kit closed-loop learning (train -> explore -> screen
# -> label -> convert) starting from the 02-aimd filtered mother set.
#
# Input : $AI2KIT_CFG_DIR/aimd.xyz  (labeled extxyz from stage 02)
# Output: $AI2KIT_AL_DIR/dp-init-data/        initial DeepMD training set
#         $AI2KIT_AL_DIR/lammps-data/         LAMMPS starting structures
#         $AI2KIT_AL_DIR/iter-*/              per-round artifacts
#             deepmd/model-*/compress.pb      committee models (grow each round)
#             lammps/job-*/dump.lammpstrj     exploration trajectories
#             screening/good|decent|poor.xyz  model-deviation screening
#             cp2k/job-*/output               real CP2K single-point labels
#             new-dataset/                    converted labels (grow the set)
#         $AI2KIT_AL_DIR/al.done              marker
#
# Config templates under config/{deepmd,lammps,cp2k} are copied into
# $AI2KIT_CFG_DIR with @ENV_SH@ / @PLUGIN_PATH@ substituted, mirroring the
# reference `config/` layout next to aimd.xyz.
# Idempotent: skips when al.done exists; each workflow script is itself
# idempotent via *.done markers.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../env.sh"

AL_DIR="$AI2KIT_AL_DIR"
CFG_DIR="$AI2KIT_CFG_DIR"
WORK_DIR="$AL_DIR"
CONFIG_DIR="$CFG_DIR"

if [ -f "$AL_DIR/al.done" ]; then
    echo "[03-active-learning] already done -> skip"
    exit 0
fi

# --- 0. prerequisites ------------------------------------------------------
if [ ! -f "$AI2KIT_AIMD_DIR/aimd.done" ] || [ ! -s "$CFG_DIR/aimd.xyz" ]; then
    echo "[03-active-learning] ERROR: 02-aimd not complete" >&2
    exit 1
fi

# --- 1. config templates -> $AI2KIT_CFG_DIR (substitute env.sh + plugin) ---
mkdir -p "$CFG_DIR"
rm -rf "$CFG_DIR/deepmd" "$CFG_DIR/lammps" "$CFG_DIR/cp2k"
cp -r "$EXPERT_DIR/03-active-learning/config/deepmd" "$CFG_DIR/"
cp -r "$EXPERT_DIR/03-active-learning/config/lammps" "$CFG_DIR/"
cp -r "$EXPERT_DIR/03-active-learning/config/cp2k"   "$CFG_DIR/"

ENV_SH="$EXPERT_DIR/env.sh"
PLUGIN="${DEEPMD_PLUGIN:-libdeepmd_lmp.so}"
sed -i "s|@ENV_SH@|$ENV_SH|g" \
    "$CFG_DIR/deepmd/slurm-header.sh" \
    "$CFG_DIR/lammps/slurm-header.sh" \
    "$CFG_DIR/cp2k/slurm-header.sh"
sed -i "s|@PLUGIN_PATH@|$PLUGIN|g" "$CFG_DIR/lammps/lammps.in"

# --- 2. workflow knobs (env-gated by env.sh) -------------------------------
export CONFIG_DIR="$CFG_DIR"
export WORK_DIR="$AL_DIR"
export TYPE_MAP="[O,H]"
export MODEL_NUM="$AI2KIT_MODEL_NUM"
export MD_WORKERS="${AI2KIT_MD_WORKERS:-4}"
export LABEL_WORKERS="${AI2KIT_LABEL_WORKERS:-4}"
export MD_TEMP="$AI2KIT_MD_TEMP"
export MODEL_DEVI_COND="$AI2KIT_MODEL_DEVI_COND"
export DEVI_SLICE="$AI2KIT_DEVI_SLICE"
export DECAY_STEPS="$AI2KIT_DECAY_STEPS"
export MAX_LABEL="$AI2KIT_MAX_LABEL"
export USE_BAD_CONFS=0
export UPDATE_MD_CONFS=0
export TRAIN_STEPS="$AI2KIT_TRAIN_STEPS"
export MD_STEPS="$AI2KIT_MD_STEPS"
export SAMPLE_FREQ="$AI2KIT_SAMPLE_FREQ"
export NUMB_TEST="$AI2KIT_NUMB_TEST"
export DISP_FREQ="$AI2KIT_DISP_FREQ"
export SAVE_FREQ="$AI2KIT_SAVE_FREQ"
export SETUP_SAMPLE="$AI2KIT_SETUP_SAMPLE"
export LAMMPS_SAMPLE="$AI2KIT_LAMMPS_SAMPLE"

# --- 3. setup (initial data) -----------------------------------------------
echo "[03-active-learning] setup"
bash "$EXPERT_DIR/03-active-learning/workflow/setup.sh"

# --- 4. active-learning rounds ---------------------------------------------
ROUNDS="$AI2KIT_AL_ROUNDS"
for ((iter=1; iter<=ROUNDS; iter++)); do
    ITER_NAME="$(printf "%03d" "$iter")"
    echo "[03-active-learning] === AL iteration $ITER_NAME ==="
    set +e
    ITER_NAME="$ITER_NAME" bash "$EXPERT_DIR/03-active-learning/workflow/iter-classic-dp-lammps-cp2k.sh"
    rc=$?
    set -e
    if [ "$rc" -eq 1 ]; then
        echo "[03-active-learning] iteration $ITER_NAME: no decent structures -> converged/stopped"
        break
    fi
    if [ "$rc" -ne 0 ]; then
        echo "[03-active-learning] ERROR: iteration $ITER_NAME failed (rc=$rc)" >&2
        exit "$rc"
    fi
done

touch "$AL_DIR/al.done"
echo "[03-active-learning] done -> $AL_DIR"
