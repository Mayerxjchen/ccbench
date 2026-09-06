#!/bin/bash

# ============================================================
# ai2-kit DeepMD 主动学习迭代脚本
#
# 作用：
#   执行一轮完整的主动学习迭代，包含 5 个步骤：
#   1. DeepMD 训练（多模型）
#   2. LAMMPS 分子动力学探索
#   3. 模型偏差筛选（screening）
#   4. CP2K DFT 标注（labeling）
#   5. 数据转换（dpdata）
#
# 用法：
#   ITER_NAME="001" ./workflow/iter-classic-dp-lammps-cp2k.sh
#
# 需要的环境变量：
#   ITER_NAME, CONFIG_DIR, WORK_DIR, TYPE_MAP,
#   TRAIN_STEPS, DECAY_STEPS, MODEL_NUM,
#   MD_STEPS, MD_TEMP, MD_WORKERS, SAMPLE_FREQ,
#   MODEL_DEVI_COND, USE_BAD_CONFS, UPDATE_MD_CONFS,
#   LABEL_WORKERS, MAX_LABEL
# ============================================================

set -eu

# 工具路径
AI2_KIT=/public/home/<site-user>/.conda/envs/ai2kit/bin/ai2-kit
OMB=/public/home/<site-user>/.conda/envs/ai2kit/bin/omb

# 检查环境变量
$OMB shell require-env ITER_NAME CONFIG_DIR WORK_DIR TYPE_MAP \
    TRAIN_STEPS DECAY_STEPS MODEL_NUM \
    MD_STEPS MD_TEMP MD_WORKERS SAMPLE_FREQ \
    MODEL_DEVI_COND USE_BAD_CONFS UPDATE_MD_CONFS \
    LABEL_WORKERS MAX_LABEL

# 初始化迭代目录
ITER_DIR=$WORK_DIR/iter-$ITER_NAME
mkdir -p $ITER_DIR

[ -f $ITER_DIR/iter.done ] && echo "iteration $ITER_NAME already done" && exit 0
echo "starting iteration at $ITER_DIR"

# Step 1: DeepMD 训练
DP_DIR=$ITER_DIR/deepmd
mkdir -p $DP_DIR

[ -f $DP_DIR/setup.done ] && echo "skip deepmd setup" || {
    # 生成 MODEL_NUM 个不重复的随机种子，训练多个模型
    $OMB combo \
        add_randint SEED -n $MODEL_NUM -a 0 -b 999999 --uniq - \
        add_var TRAIN_STEPS $TRAIN_STEPS - \
        add_var DECAY_STEPS $DECAY_STEPS - \
        add_file_set DP_DATASET "$WORK_DIR/dp-init-data/*" "$WORK_DIR/iter-*/new-dataset/*" --format json-item --abs - \
        make_files $DP_DIR/model-{i}/input.json --template $CONFIG_DIR/deepmd/input.json - \
        make_files $DP_DIR/model-{i}/run.sh     --template $CONFIG_DIR/deepmd/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$DP_DIR/model-*" - \
        add_header_files $CONFIG_DIR/deepmd/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make $DP_DIR/model-{i}/dp-train.slurm

    touch $DP_DIR/setup.done
}

$OMB job slurm submit "$DP_DIR/model-*/dp-train*.slurm" --max_tries 2 --wait --recovery $DP_DIR/slurm-recovery.json

# Step 2: LAMMPS 分子动力学探索
LMP_DIR=$ITER_DIR/lammps
mkdir -p $LMP_DIR

[ -f $LMP_DIR/setup.done ] && echo "skip lammps setup" || {
    $OMB combo \
        add_files DATA_FILE "$WORK_DIR/lammps-data/*" --abs - \
        add_file_set DP_MODELS "$DP_DIR/model-*/compress.pb" --abs - \
        add_var TEMP $MD_TEMP - \
        add_var STEPS $MD_STEPS - \
        add_var SAMPLE_FREQ $SAMPLE_FREQ - \
        add_randint SEED -n 10000 -a 0 -b 99999 --uniq - \
        set_broadcast SEED - \
        make_files $LMP_DIR/job-{TEMP}K-{i:03d}/lammps.in --template $CONFIG_DIR/lammps/lammps.in - \
        make_files $LMP_DIR/job-{TEMP}K-{i:03d}/run.sh    --template $CONFIG_DIR/lammps/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$LMP_DIR/job-*" - \
        add_header_files $CONFIG_DIR/lammps/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make $LMP_DIR/lammps-{i}.slurm --concurrency $MD_WORKERS

    touch $LMP_DIR/setup.done
}

$OMB job slurm submit "$LMP_DIR/lammps*.slurm" --max_tries 2 --wait --recovery $LMP_DIR/slurm-recovery.json

# Step 3: 模型偏差筛选（Screening）
SCREENING_DIR=$ITER_DIR/screening
mkdir -p $SCREENING_DIR

[ -f $SCREENING_DIR/screening.done ] && echo "skip screening" || {
    # 使用 ai2-kit model_devi 工具筛选构型
    $AI2_KIT tool model_devi \
        read "$LMP_DIR/job-*/" --traj_file dump.lammpstrj --md_file model_devi.out --specorder "$TYPE_MAP" --ignore_error - \
        slice "10:" - \
        grade $MODEL_DEVI_COND --col max_devi_f - \
        dump_stats $SCREENING_DIR/stats.tsv - \
        write $SCREENING_DIR/good.xyz   --level good - \
        write $SCREENING_DIR/decent.xyz --level decent - \
        write $SCREENING_DIR/poor.xyz   --level poor - \
        done

    touch $SCREENING_DIR/screening.done
}

cat $SCREENING_DIR/stats.tsv

# 退出条件：没有 decent 构型
if [ ! -s $SCREENING_DIR/decent.xyz ]; then
    echo "no decent structure found, iteration is considered as done"
    touch $ITER_DIR/iter.done
    exit 1
fi

# Step 4: CP2K DFT 标注（Labeling）
LABELING_DIR=$ITER_DIR/cp2k
mkdir -p $LABELING_DIR

[ -f $LABELING_DIR/setup.done ] && echo "skip cp2k setup" || {
    # 从 decent.xyz 中采样 MAX_LABEL 个结构
    $AI2_KIT tool ase read $SCREENING_DIR/decent.xyz - sample $MAX_LABEL - \
        write_frames $LABELING_DIR/data/{i:03d}.inc --format cp2k-inc

    # 如果 USE_BAD_CONFS > 0，也会标注一些 poor 构型
    [ $USE_BAD_CONFS -gt 0 ] && {
        $AI2_KIT tool ase read $SCREENING_DIR/poor.xyz - sample $USE_BAD_CONFS --method random - \
            write_frames $LABELING_DIR/data/bad-{i:03d}.inc --format cp2k-inc
    }

    $OMB combo \
        add_files DATA_FILE "$LABELING_DIR/data/*" --abs - \
        make_files $LABELING_DIR/job-{i:03d}/cp2k.inp --template $CONFIG_DIR/cp2k/cp2k.inp - \
        make_files $LABELING_DIR/job-{i:03d}/run.sh   --template $CONFIG_DIR/cp2k/run.sh --mode 755 - \
        done

    $OMB batch \
        add_work_dirs "$LABELING_DIR/job-*" - \
        add_header_files $CONFIG_DIR/cp2k/slurm-header.sh - \
        add_cmds "bash ./run.sh" - \
        make $LABELING_DIR/job-{i:03d}/cp2k.slurm --concurrency $LABEL_WORKERS

    touch $LABELING_DIR/setup.done
}

$OMB job slurm submit "$LABELING_DIR/job-*/cp2k*.slurm" --max_tries 2 --wait --recovery $LABELING_DIR/slurm-recovery.json

# Step 5: 数据转换（dpdata）
$AI2_KIT tool dpdata read $LABELING_DIR/job-*/output --fmt='cp2k/output' --type_map="$TYPE_MAP" - write $ITER_DIR/new-dataset

# 可选：更新 MD 初始结构
[ $UPDATE_MD_CONFS -gt 0 ] && {
    rm -f $WORK_DIR/lammps-data/* || true
    $AI2_KIT tool ase read $SCREENING_DIR/good.xyz - \
        sample $UPDATE_MD_CONFS --method random - \
        write_frames $WORK_DIR/lammps-data/{i:03d}.data --format lammps-data --specorder "$TYPE_MAP"
}

# 标记本轮迭代完成
touch $ITER_DIR/iter.done
