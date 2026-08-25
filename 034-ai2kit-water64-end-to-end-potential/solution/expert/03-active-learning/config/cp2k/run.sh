#!/bin/bash
# ============================================================================
# CP2K single-point label runner (per-frame), container-adapted.
# @DATA_FILE is substituted by the omb combo with the absolute path to the
# frame's coord_n_cell.inc; we symlink it in place and run cp2k.psmp.
# success.flag / error.flag / cp2k.done markers; output is CP2K stdout, which
# ai2-kit's dpdata reader (`--fmt=cp2k/output`) parses for E + forces.
# ============================================================================
set -e
source "${EXPERT_DIR:?}/hpc-runtime.sh"

ln -sf @DATA_FILE coord_n_cell.inc

[ -f cp2k.done ] || {
    if ai2kit_run_cp2k "${SLURM_NTASKS:-4}" cp2k.inp output; then
        touch success.flag
    else
        touch error.flag
        exit 1
    fi

    rm -f *.wfn || true
    touch cp2k.done
}
