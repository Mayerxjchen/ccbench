#!/bin/bash
# ============================================================================
# iter-classic-dp-lammps-cp2k.sh — one complete active-learning round:
#   1. DeepMD train (multi-model committee)  via omb combo/batch/job + sbatch
#   2. LAMMPS MD exploration                  via omb + sbatch
#   3. model-deviation screening              via ai2-kit tool model_devi
#   4. CP2K DFT labeling                      via omb + sbatch
#   5. data conversion (dpdata -> new-dataset)
#
# Usage: ITER_NAME="001" ./workflow/iter-classic-dp-lammps-cp2k.sh
#
# Required env: ITER_NAME, CONFIG_DIR, WORK_DIR, TYPE_MAP, TRAIN_STEPS,
#   DECAY_STEPS, NUMB_TEST, DISP_FREQ, SAVE_FREQ, MODEL_NUM, MD_STEPS, MD_TEMP,
#   MD_WORKERS, SAMPLE_FREQ, MODEL_DEVI_COND, DEVI_SLICE, USE_BAD_CONFS,
#   UPDATE_MD_CONFS, LABEL_WORKERS, MAX_LABEL
#
# Adapted from reference/expert-trajectory/active-learning/workflow/
# iter-classic-dp-lammps-cp2k.sh: ai2-kit/omb resolved from PATH; env.sh sourced
# by the generated .slurm headers; every job goes through pseudo-slurm (sbatch)
# and is awaited with `omb job slurm submit --wait` exactly like the reference.
# Exit code 1 == "no decent structures, iteration converged" (caller stops).
# Idempotent per sub-step via *.done markers and per iteration via iter.done.
# ============================================================================
set -eu
source "$(dirname "$0")/../../env.sh"

AI2_KIT=$(command -v ai2-kit)
OMB=$(command -v omb)

$OMB shell require-env ITER_NAME CONFIG_DIR WORK_DIR TYPE_MAP \
    TRAIN_STEPS DECAY_STEPS NUMB_TEST DISP_FREQ SAVE_FREQ MODEL_NUM \
    MD_STEPS MD_TEMP MD_WORKERS SAMPLE_FREQ \
    MODEL_DEVI_COND DEVI_SLICE USE_BAD_CONFS UPDATE_MD_CONFS \
    LABEL_WORKERS MAX_LABEL

ITER_DIR=$WORK_DIR/iter-$ITER_NAME
mkdir -p "$ITER_DIR"

[ -f "$ITER_DIR/iter.done" ] && { echo "iteration $ITER_NAME already done"; exit 0; }
echo "starting iteration at $ITER_DIR"

# ---------------------------------------------------------------- Step 1: train
DP_DIR=$ITER_DIR/deepmd
mkdir -p "$DP_DIR"

if [ -f "$DP_DIR/setup.done" ]; then
    echo "skip deepmd setup"
else
    $OMB combo \
        add_randint SEED -n $MODEL_NUM -a 0 -b 999999 --uniq - \
        add_var TRAIN_STEPS $TRAIN_STEPS - \
        add_var DECAY_STEPS $DECAY_STEPS - \
        add_var NUMB_TEST $NUMB_TEST - \
        add_var DISP_FREQ $DISP_FREQ - \
        add_var SAVE_FREQ $SAVE_FREQ - \
        add_file_set DP_DATASET "$WORK_DIR/dp-init-data/*" "$WORK_DIR/iter-*/new-dataset/*" --format json-item --abs - \
        make_files $DP_DIR/model-{i}/input.json --template $CONFIG_DIR/deepmd/input.json - \
        make_files $DP_DIR/model-{i}/run.sh     --template $CONFIG_DIR/deepmd/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$DP_DIR/model-*" - \
        add_header_files $CONFIG_DIR/deepmd/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make "$DP_DIR/model-{i}/dp-train.slurm"

    touch "$DP_DIR/setup.done"
fi

$OMB job slurm submit "$DP_DIR/model-*/dp-train*.slurm" --max_tries 2 --wait --recovery "$DP_DIR/slurm-recovery.json"

# ---------------------------------------------------------------- Step 2: explore
LMP_DIR=$ITER_DIR/lammps
mkdir -p "$LMP_DIR"

if [ -f "$LMP_DIR/setup.done" ]; then
    echo "skip lammps setup"
else
    $OMB combo \
        add_files DATA_FILE "$WORK_DIR/lammps-data/*" --abs - \
        add_file_set DP_MODELS "$DP_DIR/model-*/compress.pb" --abs - \
        add_var TEMP $MD_TEMP - \
        add_var STEPS $MD_STEPS - \
        add_var SAMPLE_FREQ $SAMPLE_FREQ - \
        add_randint SEED -n 10000 -a 0 -b 99999 --uniq - \
        set_broadcast SEED - \
        make_files "$LMP_DIR/job-{TEMP}K-{i:03d}/lammps.in" --template $CONFIG_DIR/lammps/lammps.in - \
        make_files "$LMP_DIR/job-{TEMP}K-{i:03d}/run.sh"    --template $CONFIG_DIR/lammps/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$LMP_DIR/job-*" - \
        add_header_files $CONFIG_DIR/lammps/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make "$LMP_DIR/lammps-{i}.slurm" --concurrency $MD_WORKERS

    touch "$LMP_DIR/setup.done"
fi

$OMB job slurm submit "$LMP_DIR/lammps*.slurm" --max_tries 2 --wait --recovery "$LMP_DIR/slurm-recovery.json"

# ---------------------------------------------------------------- Step 3: screen
SCREENING_DIR=$ITER_DIR/screening
mkdir -p "$SCREENING_DIR"

if [ -f "$SCREENING_DIR/screening.done" ]; then
    echo "skip screening"
else
    $AI2_KIT tool model_devi \
        read "$LMP_DIR/job-*/" --traj_file dump.lammpstrj --md_file model_devi.out --specorder "$TYPE_MAP" --ignore_error - \
        slice "$DEVI_SLICE" - \
        grade $MODEL_DEVI_COND --col max_devi_f - \
        dump_stats "$SCREENING_DIR/stats.tsv" - \
        write "$SCREENING_DIR/good.xyz"   --level good - \
        write "$SCREENING_DIR/decent.xyz" --level decent - \
        write "$SCREENING_DIR/poor.xyz"   --level poor - \
        done

    touch "$SCREENING_DIR/screening.done"
fi

cat "$SCREENING_DIR/stats.tsv"

# exit condition: no decent structures
if [ ! -s "$SCREENING_DIR/decent.xyz" ]; then
    echo "no decent structure found, iteration is considered as done"
    touch "$ITER_DIR/iter.done"
    exit 1
fi

# ---------------------------------------------------------------- Step 4: label
LABELING_DIR=$ITER_DIR/cp2k
mkdir -p "$LABELING_DIR"

if [ -f "$LABELING_DIR/setup.done" ]; then
    echo "skip cp2k setup"
else
    $AI2_KIT tool ase read "$SCREENING_DIR/decent.xyz" - sample "$MAX_LABEL" - \
        write_frames "$LABELING_DIR/data/{i:03d}.inc" --format cp2k-inc

    if [ "$USE_BAD_CONFS" -gt 0 ]; then
        $AI2_KIT tool ase read "$SCREENING_DIR/poor.xyz" - sample "$USE_BAD_CONFS" --method random - \
            write_frames "$LABELING_DIR/data/bad-{i:03d}.inc" --format cp2k-inc
    fi

    $OMB combo \
        add_files DATA_FILE "$LABELING_DIR/data/*" --abs - \
        make_files "$LABELING_DIR/job-{i:03d}/cp2k.inp" --template $CONFIG_DIR/cp2k/cp2k.inp - \
        make_files "$LABELING_DIR/job-{i:03d}/run.sh"   --template $CONFIG_DIR/cp2k/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$LABELING_DIR/job-*" - \
        add_header_files $CONFIG_DIR/cp2k/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make "$LABELING_DIR/job-{i:03d}/cp2k.slurm" --concurrency $LABEL_WORKERS

    touch "$LABELING_DIR/setup.done"
fi

$OMB job slurm submit "$LABELING_DIR/job-*/cp2k*.slurm" --max_tries 2 --wait --recovery "$LABELING_DIR/slurm-recovery.json"

# ---------------------------------------------------------------- Step 5: convert
$AI2_KIT tool dpdata read "$LABELING_DIR/job-*/output" --fmt='cp2k/output' --type_map="$TYPE_MAP" - write "$ITER_DIR/new-dataset"

# optionally refresh the LAMMPS starting structures
if [ "$UPDATE_MD_CONFS" -gt 0 ]; then
    rm -f "$WORK_DIR/lammps-data/"* || true
    $AI2_KIT tool ase read "$SCREENING_DIR/good.xyz" - \
        sample "$UPDATE_MD_CONFS" --method random - \
        write_frames "$WORK_DIR/lammps-data/{i:03d}.data" --format lammps-data --specorder "$TYPE_MAP"
fi

touch "$ITER_DIR/iter.done"
echo "iteration $ITER_NAME done"
