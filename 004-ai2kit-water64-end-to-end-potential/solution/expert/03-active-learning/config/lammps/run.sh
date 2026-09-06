#!/bin/bash
# ============================================================================
# LAMMPS MD exploration runner (per-job), container-adapted.
# Detects restart files for continuation; idempotent via lammps.done.
# Sourced directly (this sub-job runs as `bash ./run.sh`, a NEW process, so the
# header's env vars are inherited but shell functions are not — same pattern as
# config/cp2k/run.sh). ai2kit_lmp prepends the plugin-ABI LD_LIBRARY_PATH
# (libtensorflow_cc.so.2 + deepmd lib dir) then runs AI2KIT_LAMMPS_BIN; bare
# `lmp` is a container-only convenience that does NOT resolve on the cluster.
# ============================================================================
set -e
source "${EXPERT_DIR:?}/hpc-runtime.sh"

[ -f lammps.done ] || {
    ls md.restart.* &>/dev/null && RESTART=1 || RESTART=0

    ai2kit_lmp -i lammps.in -v restart $RESTART

    touch lammps.done
}
