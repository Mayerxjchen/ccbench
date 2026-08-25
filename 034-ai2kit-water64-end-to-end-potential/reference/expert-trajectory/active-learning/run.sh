#!/bin/bash

# ============================================================
# ai2-kit DeepMD 主动学习主执行脚本
#
# 作用：
#   1. 设置所有必需的环境变量
#   2. 运行 setup 生成初始训练数据
#   3. 启动主动学习迭代（5 轮，与 tesla-h2o 一致）
#
# 用法：
#   cd /public/home/<site-user>/ai2kit/ai2kit
#   bash run.sh
# ============================================================

set -eu

# ============================================================
# 1. 环境配置
# ============================================================

export CONFIG_DIR=./config
export WORK_DIR=./workdir
export TYPE_MAP="[O,H]"

# ============================================================
# 2. 运行 setup（生成初始数据）
# ============================================================

./workflow/setup.sh

# ============================================================
# 3. 主动学习通用参数
# ============================================================

export MODEL_NUM=4
export MD_WORKERS=10
export LABEL_WORKERS=20
export MD_TEMP="330 430 530"
export MODEL_DEVI_COND="--lo 0.2 --hi 0.4"
export DECAY_STEPS=1000
export MAX_LABEL=20
export USE_BAD_CONFS=0
export UPDATE_MD_CONFS=0

# ============================================================
# 4. Iteration 001：短程探索
# ============================================================

export TRAIN_STEPS=100000
export MD_STEPS=1000
export SAMPLE_FREQ=10

ITER_NAME="001" ./workflow/iter-classic-dp-lammps-cp2k.sh

# ============================================================
# 5. Iteration 002：增加 MD 步数
# ============================================================

export MD_STEPS=4000
export SAMPLE_FREQ=100

ITER_NAME="002" ./workflow/iter-classic-dp-lammps-cp2k.sh

# ============================================================
# 6. Iteration 003-005：长程 MD，更多标注
# ============================================================

export TRAIN_STEPS=400000
export DECAY_STEPS=2000
export MD_STEPS=100000
export SAMPLE_FREQ=100
export UPDATE_MD_CONFS=2
export MAX_LABEL=50
export MD_TEMP="330 430 530 630"

ITER_NAME="003" ./workflow/iter-classic-dp-lammps-cp2k.sh
ITER_NAME="004" ./workflow/iter-classic-dp-lammps-cp2k.sh
ITER_NAME="005" ./workflow/iter-classic-dp-lammps-cp2k.sh

# ============================================================
# 完成后
# ============================================================
#
# 5 轮迭代完成后，检查：
#   1. workdir/iter-005/deepmd/model-*/compress.pb（最终模型）
#   2. workdir/iter-005/screening/stats.tsv（筛选统计）
#   3. model_devi.out（模型偏差趋势）
