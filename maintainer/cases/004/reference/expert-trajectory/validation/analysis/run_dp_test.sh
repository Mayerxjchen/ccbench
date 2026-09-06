#!/bin/bash
#SBATCH --job-name=dp-test
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --output=/public/home/<site-user>/ai2kit/ai2kit/test/dp-test/output/slurm.out
#SBATCH --error=/public/home/<site-user>/ai2kit/ai2kit/test/dp-test/output/slurm.out

# ============================================================
# dp test: 数据准备 + 模型精度验证 + 绘图（自动跳过已完成步骤）
#
# 用法:
#   sbatch test/dp-test/run_dp_test.sh              # 全流程（自动跳过已完成）
#   sbatch test/dp-test/run_dp_test.sh --force       # 强制重新执行所有步骤
#   bash  test/dp-test/run_dp_test.sh --plot-only    # 只绘图
#   bash  test/dp-test/run_dp_test.sh --prepare-only # 只准备测试数据
# ============================================================
set -e

PROJECT_DIR="/public/home/<site-user>/ai2kit/ai2kit"
TEST_DATA="$PROJECT_DIR/test/dp-test/test-data/O64H128"
RESULT_DIR="$PROJECT_DIR/test/dp-test/output"
MODEL_DIR="$PROJECT_DIR/workdir/iter-005/deepmd"
AIMD_XYZ="$PROJECT_DIR/config/aimd.xyz"

# ---- 解析参数 ----
PLOT_ONLY=false
PREPARE_ONLY=false
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --plot-only)    PLOT_ONLY=true ;;
        --prepare-only) PREPARE_ONLY=true ;;
        --force)        FORCE=true ;;
    esac
done

# ---- 环境 ----
source /etc/profile.d/modules.sh
module load anaconda/2022.5
eval "$(conda shell.bash hook)"

activate_deepmd() {
    conda activate /public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118
    export LD_LIBRARY_PATH=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/lib:$LD_LIBRARY_PATH
    export XLA_FLAGS=--xla_gpu_cuda_data_dir=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/lib
    export OMP_NUM_THREADS=4
    export TF_INTER_OP_PARALLELISM_THREADS=1
    export TF_INTRA_OP_PARALLELISM_THREADS=4
}

mkdir -p "$RESULT_DIR"

# ============================================================
# Step 1: 准备测试数据
# ============================================================
step_prepare() {
    if [[ -f "$TEST_DATA/set.000/coord.npy" && "$FORCE" == "false" ]]; then
        echo ">>> [Step 1] 测试数据已存在，跳过: $TEST_DATA"
        return 0
    fi
    echo "============================================"
    echo ">>> [Step 1] 准备测试数据"
    echo "============================================"
    conda activate ai2kit
    python "$PROJECT_DIR/test/dp-test/prepare_test_data.py"
    echo ""
}

# ============================================================
# Step 2: dp test 模型精度验证
# ============================================================
step_dp_test() {
    local all_done=true
    for model_id in 0 1 2 3; do
        local result_prefix="$RESULT_DIR/model-$model_id"
        if [[ ! -f "${result_prefix}.e_peratom.out" || ! -f "${result_prefix}.f.out" ]]; then
            all_done=false
            break
        fi
    done

    if [[ "$all_done" == "true" && "$FORCE" == "false" ]]; then
        echo ">>> [Step 2] dp test 结果已存在，跳过"
        return 0
    fi

    echo "============================================"
    echo ">>> [Step 2] dp test 模型精度验证"
    echo "测试集: $TEST_DATA"
    echo "============================================"
    activate_deepmd

    for model_id in 0 1 2 3; do
        model_pb="$MODEL_DIR/model-$model_id/compress.pb"
        result_prefix="$RESULT_DIR/model-$model_id"

        if [ ! -f "$model_pb" ]; then
            echo "[model-$model_id] 模型文件不存在, 跳过"
            continue
        fi

        echo ""
        echo ">>> model-$model_id"

        dp test \
            -m "$model_pb" \
            -s "$TEST_DATA" \
            -d "$result_prefix" \
            -n 0

        n_e=$(wc -l < "${result_prefix}.e_peratom.out")
        n_f=$(wc -l < "${result_prefix}.f.out")
        echo "    energy: $n_e 帧, forces: $n_f 帧"
    done
    echo ""
}

# ============================================================
# Step 3: 绘制 parity plot
# ============================================================
step_plot() {
    echo "============================================"
    echo ">>> [Step 3] 绘制 parity plot"
    echo "============================================"

    conda activate ai2kit
    pip install matplotlib fire -q

    python3 "$PROJECT_DIR/test/dp-test/dp-test.py" \
        --result_prefix="$RESULT_DIR/model-*" \
        --output="$RESULT_DIR/dp-test.png"

    # 清理中间文件
    rm -f "$RESULT_DIR"/model-*.e.out "$RESULT_DIR"/model-*.v.out "$RESULT_DIR"/model-*.v_peratom.out

    echo ">>> 完成: $RESULT_DIR/dp-test.png"
}

# ============================================================
# 执行
# ============================================================
if [[ "$PLOT_ONLY" == "true" ]]; then
    step_plot
    exit 0
fi

if [[ "$PREPARE_ONLY" == "true" ]]; then
    step_prepare
    exit 0
fi

# 全流程
step_prepare
step_dp_test
step_plot
