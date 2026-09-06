#!/bin/bash
# ============================================================================
# prod-dp-lammps.sh — production LAMMPS run against the final committee.
# Not invoked by 03-active-learning/run.sh by default; kept as the expert's
# utility for generating a long MLP trajectory.  Container-adapted: ai2-kit/omb
# from PATH, env.sh sourced by the generated .slurm headers.
#
# Required env: WORK_DIR, CONFIG_DIR, PROD_DIR, DP_MODELS, MD_STEPS, MD_TEMP,
#   DATA_FILE, SAMPLE_FREQ
# ============================================================================
set -eu
source "$(dirname "$0")/../../env.sh"

AI2_KIT=$(command -v ai2-kit)
OMB=$(command -v omb)

$OMB shell require-env WORK_DIR CONFIG_DIR PROD_DIR \
    DP_MODELS MD_STEPS MD_TEMP DATA_FILE SAMPLE_FREQ

mkdir -p "$PROD_DIR"

if [ -f "$PROD_DIR/setup.done" ]; then
    echo "skip lammps setup"
else
    $OMB combo \
        add_files DATA_FILE "$DATA_FILE" --abs - \
        add_file_set DP_MODELS "$DP_MODELS" --abs - \
        add_var TEMP $MD_TEMP - \
        add_var STEPS "$MD_STEPS" - \
        add_var SAMPLE_FREQ "$SAMPLE_FREQ" - \
        add_var SEED "$(ai2kit_derived_seed "prod:$(basename "$PROD_DIR")")" - \
        set_broadcast SEED - \
        make_files "$PROD_DIR/job-{TEMP}K/lammps.in" --template "$CONFIG_DIR/lammps/lammps.in" - \
        make_files "$PROD_DIR/job-{TEMP}K/run.sh" --template "$CONFIG_DIR/lammps/run.sh" --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$PROD_DIR/job-*" - \
        add_header_files "$CONFIG_DIR/lammps/slurm-header.sh" - \
        add_cmds "bash ./run.sh" - \
        make "$PROD_DIR/lammps-{i}.slurm"

    touch "$PROD_DIR/setup.done"
fi

if [ -f "$PROD_DIR/run.done" ]; then
    echo "skip lammps run"
else
    $OMB job slurm submit "$PROD_DIR/lammps*.slurm" --max_tries 2 --wait --recovery "$PROD_DIR/slurm-recovery.json"
    touch "$PROD_DIR/run.done"
fi
