#!/bin/bash
# ============================================================================
# LAMMPS MD exploration runner (per-job), container-adapted.
# Detects restart files for continuation; idempotent via lammps.done.
# lmp is on PATH (deepmd-kit[lmp]) and env.sh sets LAMMPS_PLUGIN_PATH.
# ============================================================================
set -e

[ -f lammps.done ] || {
    ls md.restart.* &>/dev/null && RESTART=1 || RESTART=0

    lmp -i lammps.in -v restart $RESTART

    touch lammps.done
}
