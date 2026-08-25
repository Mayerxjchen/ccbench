#!/bin/bash

# ============================================================
# LAMMPS 分子动力学运行脚本（tesla-h2o 版本）
#
# 作用：
#   1. 检查是否已完成（避免重复运行）
#   2. 检测是否有 restart 文件（支持断点续算）
#   3. 运行 LAMMPS MD 模拟
#
# 功能：
#   - 幂等设计：通过 lammps.done 标记文件避免重复运行
#   - 支持 restart：自动检测 md.restart.* 文件
#   - 使用 lmp_mpi（MPI 版本）
#   - 失败时立即退出（set -e）
#
# 用法：
#   由 ai2-kit 工作流自动调用
# ============================================================

set -e
# 任何命令失败立即退出

[ -f lammps.done ] || {
    # 如果 lammps.done 不存在，执行 LAMMPS 计算

    ls md.restart.* &>/dev/null && RESTART=1 || RESTART=0
    # 检查是否有 restart 文件
    # 如果有: RESTART=1（从 restart 继续）
    # 如果没有: RESTART=0（从头开始）

    lmp -i lammps.in -v restart $RESTART
    # 运行 LAMMPS（DeepMD 版本）
    # -i lammps.in: 输入脚本
    # -v restart $RESTART: 传递 restart 变量给输入脚本

    touch lammps.done
    # 创建完成标记文件
}
