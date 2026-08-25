#!/bin/bash

# ============================================================
# CP2K DFT 计算运行脚本（tesla-h2o 版本）
#
# 作用：
#   1. 创建坐标和晶胞符号链接
#   2. 检查是否已完成（避免重复计算）
#   3. 运行 CP2K 进行 DFT 单点能计算
#   4. 清理波函数文件（节省磁盘空间）
#
# 功能：
#   - 幂等设计：通过 cp2k.done 标记文件避免重复运行
#   - 支持 MPI 并行
#   - 成功/失败分别创建标记文件
#   - 失败时立即退出（set -e）
#
# 用法：
#   由 ai2-kit 工作流自动调用
# ============================================================

set -e
# 任何命令失败立即退出

ln -sf @DATA_FILE coord_n_cell.inc
# 创建数据文件的符号链接
# @DATA_FILE 会被 ai2-kit 替换为实际的 xyz 文件路径
# coord_n_cell.inc: CP2K 输入文件引用的坐标和晶胞文件

[ -f cp2k.done ] || {
    # 如果 cp2k.done 不存在，执行 CP2K 计算

    if mpirun cp2k.psmp -i cp2k.inp &> output; then
        touch success.flag
    else
        touch error.flag
        exit 1
    fi
    # 使用 MPI 运行 CP2K
    # cp2k.psmp: CP2K MPI 版本可执行文件
    # -i cp2k.inp: 输入文件
    # &> output: 将 stdout 和 stderr 重定向到 output 文件
    # 成功: 创建 success.flag
    # 失败: 创建 error.flag 并退出，让 Slurm/omb 正确识别失败

    rm -f *.wfn || true
    # 删除波函数文件（.wfn）
    # 这些文件通常很大，计算完成后不需要保留
    # || true: 即使删除失败也不退出

    touch cp2k.done
    # 创建完成标记文件
}
