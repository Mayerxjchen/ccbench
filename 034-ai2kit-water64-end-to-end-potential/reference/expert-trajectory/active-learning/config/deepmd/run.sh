#!/bin/bash

# ============================================================
# DeepMD 模型训练运行脚本（tesla-h2o 版本）
#
# 作用：
#   1. 检查是否已完成训练（避免重复训练）
#   2. 训练 DeepMD 模型
#   3. 冻结模型（frozen_model.pb）
#   4. 压缩模型（compress.pb）
#
# 功能：
#   - 幂等设计：通过 train.done 标记文件避免重复训练
#   - 失败时立即退出（set -e）
#   - 生成压缩后的模型用于 LAMMPS MD
#
# 用法：
#   由 ai2-kit 工作流自动调用
# ============================================================

set -e
# 任何命令失败立即退出

[ -f "$CONDA_PREFIX/lib/libdevice.10.bc" ] && ln -sf "$CONDA_PREFIX/lib/libdevice.10.bc" ./libdevice.10.bc

[ -f train.done ] || {
    # 如果 train.done 不存在，执行训练

    dp train input.json
    # 运行 DeepMD 训练
    # input.json: 训练配置文件（包含网络结构、学习率等）

    touch train.done
    # 创建训练完成标记文件
}

dp freeze -o frozen_model.pb
# 冻结训练好的模型
# 将训练变量转为常量，生成 frozen_model.pb

dp compress -i frozen_model.pb -o compress.pb
# 压缩冻结模型
# compress.pb 用于 LAMMPS MD 探索（体积更小，推理更快）
