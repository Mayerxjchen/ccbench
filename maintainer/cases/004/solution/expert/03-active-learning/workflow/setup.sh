#!/bin/bash
# ============================================================================
# setup.sh — sample the filtered AIMD mother set into (a) the initial DeepMD
# training set and (b) LAMMPS starting structures.
#
# Input : $CONFIG_DIR/aimd.xyz  (produced by 02-aimd/run.sh)
# Output: $WORK_DIR/dp-init-data/   DeepMD system dir(s)
#         $WORK_DIR/lammps-data/    .data LAMMPS structures (000.data, ...)
#         $WORK_DIR/setup.done
#
# Adapted from reference/expert-trajectory/active-learning/workflow/setup.sh:
#   - ai2-kit / omb resolved from PATH (container venv /opt/ai2kit/bin)
#   - sample counts env-gated (AI2KIT_SETUP_SAMPLE / AI2KIT_LAMMPS_SAMPLE)
# Idempotent via setup.done.
# ============================================================================
set -e
source "$(dirname "$0")/../../env.sh"

AI2_KIT=$(command -v ai2-kit)
OMB=$(command -v omb)

$OMB shell require-env TYPE_MAP WORK_DIR CONFIG_DIR

[ -f "$WORK_DIR/setup.done" ] && { echo "setup already done, skip"; exit 0; }

AIMD_XYZ="$CONFIG_DIR/aimd.xyz"
[ -f "$AIMD_XYZ" ] || {
    echo "ERROR: $AIMD_XYZ not found."
    echo "Please finish CP2K AIMD, convert it to labeled extxyz, and write it to config/aimd.xyz first."
    exit 1
}

echo "setup: $WORK_DIR"
mkdir -p "$WORK_DIR/dp-init-data" "$WORK_DIR/lammps-data"

# sample the mother set into the initial DeepMD training set
$AI2_KIT tool ase read "$AIMD_XYZ" - sample "$AI2KIT_SETUP_SAMPLE" - to_dpdata --labeled --type_map "$TYPE_MAP" - write "$WORK_DIR/dp-init-data"

# sample a couple of frames as LAMMPS starting structures
$AI2_KIT tool ase read "$AIMD_XYZ" - sample "$AI2KIT_LAMMPS_SAMPLE" - write_frames "$WORK_DIR/lammps-data/{i:03d}.data" --format lammps-data --specorder "$TYPE_MAP"

touch "$WORK_DIR/setup.done"
echo "setup done"
