#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=nvt-300k
#SBATCH --partition=gpu,gpu-mig-2g-20gb
#SBATCH --gres=gpu:1
#SBATCH --output=/public/home/<site-user>/ai2kit/ai2kit/test/nvt/output/slurm.out
#SBATCH --error=/public/home/<site-user>/ai2kit/ai2kit/test/nvt/output/slurm.out

# ============================================================
# Phase 8b: LAMMPS NVT 稳定性验证 (300 K)
# 模型: 硬编码在 config/nvt/nvt.in 中 (iter-005 × 4)
# 阶段一: 10 ps 平衡 (20,000 步)
# 阶段二: 扩展至 90 ps (restart 续跑)
# ============================================================
set -e

PROJECT_DIR="/public/home/<site-user>/ai2kit/ai2kit"
WORK_DIR="$PROJECT_DIR/test/nvt/output"
DATA_FILE="$PROJECT_DIR/workdir/lammps-data/000.data"

# 模型路径: 用当前迭代的 4 个模型
ITER="iter-005"
DP_MODELS="$PROJECT_DIR/workdir/$ITER/deepmd/model-0/compress.pb \
$PROJECT_DIR/workdir/$ITER/deepmd/model-1/compress.pb \
$PROJECT_DIR/workdir/$ITER/deepmd/model-2/compress.pb \
$PROJECT_DIR/workdir/$ITER/deepmd/model-3/compress.pb"

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# 替换模板中的模型路径
sed "s|@DP_MODELS@|$DP_MODELS|g" "$PROJECT_DIR/config/nvt/nvt.in" > nvt.in

module load anaconda/2022.5
module load cuda/12.4
source activate /public/groups/benchmark/libs/conda/deepmd/3.1.0a0

export OMP_NUM_THREADS=4
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=4

SEED=$RANDOM

echo "============================================"
echo "Phase 8b: NVT 稳定性验证 (300 K)"
echo "初始结构: $DATA_FILE"
echo "模型: iter-005 × 4 (model deviation)"
echo "============================================"

# ============================================================
# 阶段一: 10 ps (20,000 步)
# ============================================================
echo ""
echo ">>> 阶段一: 10 ps NVT 平衡 (20,000 步) ..."

lmp -i nvt.in \
    -v restart 0 \
    -v DATA_FILE "$DATA_FILE" \
    -v TEMP 300 \
    -v N_STEPS 20000 \
    -v SEED $SEED

echo "    完成: $(grep '^[0-9]' thermo.dat 2>/dev/null | tail -1 | awk '{print $1}') 步"

# ============================================================
# 阶段二: 扩展至 90 ps (160,000 步, restart 续跑)
# ============================================================
echo ""
echo ">>> 阶段二: 扩展至 90 ps ..."

lmp -i nvt.in \
    -v restart 1 \
    -v DATA_FILE "$DATA_FILE" \
    -v TEMP 300 \
    -v N_STEPS 180000 \
    -v SEED $SEED

# ============================================================
# 清理: 只保留最终输出文件
# ============================================================
rm -f nvt.in log.lammps md.restart.* slurm.out

echo ""
echo "============================================"
echo "NVT 稳定性验证完成"
echo "总步数: $(tail -1 thermo.dat | awk '{print $1}')"
echo "轨迹帧数: $(grep -c 'ITEM: ATOMS' dump.lammpstrj)"
echo "输出目录: $WORK_DIR"
echo ""
echo "保留文件:"
echo "  dump.lammpstrj    轨迹 (给 8c RDF)"
echo "  thermo.dat        温度/能量/压力"
echo "  model_devi.out    4 模型力偏差"
echo "============================================"
