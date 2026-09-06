#!/bin/bash

# 从已经筛选合格的 config/aimd.xyz 生成 DeepMD 训练数据和 LAMMPS 初始结构
set -e

# ai2-kit 和 omb 命令路径（conda ai2kit 环境）
AI2_KIT=/public/home/<site-user>/.conda/envs/ai2kit/bin/ai2-kit
OMB=/public/home/<site-user>/.conda/envs/ai2kit/bin/omb

$OMB shell require-env TYPE_MAP WORK_DIR CONFIG_DIR

[ -f $WORK_DIR/setup.done ] && echo "setup 已完成，跳过" && exit 0

AIMD_XYZ="$CONFIG_DIR/aimd.xyz"
[ -f "$AIMD_XYZ" ] || {
    echo "ERROR: $AIMD_XYZ not found."
    echo "Please finish CP2K AIMD, convert it to labeled extxyz, and write it to config/aimd.xyz first."
    exit 1
}

echo "开始 setup: $WORK_DIR"

mkdir -p "$WORK_DIR/dp-init-data" "$WORK_DIR/lammps-data"

# 从合格 AIMD 轨迹等间距采样 50 帧，生成 DeepMD 初始数据集
$AI2_KIT tool ase read "$AIMD_XYZ" - sample 50 - to_dpdata --labeled --type_map "$TYPE_MAP" - write $WORK_DIR/dp-init-data

# 从合格 AIMD 轨迹等间距采样 2 帧，生成 LAMMPS 初始结构
$AI2_KIT tool ase read "$AIMD_XYZ" - sample 2 - write_frames $WORK_DIR/lammps-data/{i:03d}.data --format lammps-data --specorder "$TYPE_MAP"

touch $WORK_DIR/setup.done
