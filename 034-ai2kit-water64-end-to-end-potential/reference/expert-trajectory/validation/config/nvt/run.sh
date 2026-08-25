#!/bin/bash
# ============================================================
# LAMMPS NVT 运行脚本 — 支持 restart 续跑
# ============================================================
set -e

[ -f nvt.done ] || {
    ls md.restart.* &>/dev/null && RESTART=1 || RESTART=0

    lmp -i nvt.in -v restart $RESTART

    touch nvt.done
}
