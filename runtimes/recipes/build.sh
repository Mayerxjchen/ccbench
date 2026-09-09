#!/bin/bash
# CCBench 运行环境与候选沙箱镜像构建脚本
#
# 用法:
#   bash build.sh                          # 列出可用目标与当前镜像
#   bash build.sh matclaw-cips             # 构建 MatClaw CIPS CPU 镜像
#   bash build.sh 001                      # 解析用例依赖并构建
#   bash build.sh all                      # 构建隐藏 verifier 所需的本地 Docker 镜像
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
FORCE=false

usage() {
    cat <<'EOF'
按需构建 CCBench 隐藏科学 verifier 镜像。

真实可用目标 (Active Targets):
  matclaw-cips            ccbench-matclaw-cips:cpu (DeePMD + LAMMPS + CIPS teacher)
  deepmd-jax              ccbench-deepmd-jax:cpu (DP-MP / JAX runtime)
  all                     构建以上全部 verifier 镜像

注意:
  - jax-gpu 为 CompShare GPU 镜像配方 (由 recipe.lock.json 定义)，
    如需构建请使用 scripts/qualification/build_compshare_image_b.py。
  - Case 004 (AI2Kit+CP2K) 依赖集群预置 Singularity/SIF 镜像，由 HPC 控制层直接调度，
    无本地 Docker 构建目标；Candidate 在宿主机运行，远端计算由 Operator 负责。

也可传入案例目录名或编号（如 001 或 001-matclaw-cips-active-distillation），自动解析依赖。

示例:
  bash build.sh matclaw-cips
  bash build.sh 001
EOF
}

BASE_IMAGE="${CCBENCH_BASE_IMAGE:-ubuntu:24.04}"
PY="${CCBENCH_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="${CCBENCH_PYTHON:-python3}"

while getopts "fh" opt; do
    case $opt in
        f) FORCE=true ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

TARGETS=("$@")
if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "=== 当前 CCBench 镜像 ==="
    docker images 2>/dev/null | grep -E "ccbench|mlffbench|dftworld|REPOSITORY" || echo "(未连接 Docker 或无镜像)"
    echo ""
    usage
    exit 0
fi

resolve_case_dir() {
    local target="$1"
    if [ -d "$ROOT/cases/$target" ]; then
        echo "$ROOT/cases/$target"
        return 0
    fi
    if [ -d "$ROOT/$target" ]; then
        echo "$ROOT/$target"
        return 0
    fi
    local matched
    matched="$(find "$ROOT/cases" -maxdepth 1 -mindepth 1 -type d -name "${target}*" | head -n 1)"
    if [ -n "$matched" ] && [ -d "$matched" ]; then
        echo "$matched"
        return 0
    fi
    return 1
}

resolve_task_steps() {
    local task="$1"
    local case_dir
    case_dir="$(resolve_case_dir "$task")" || return 1
    "$PY" - "$case_dir" <<'PY' || return 1
import sys
from pathlib import Path
from ccbench.contracts.case import CaseSpec

case_dir = Path(sys.argv[1])
spec = CaseSpec.load(case_dir)

req_families = {getattr(req, "family", None) or getattr(req, "name", "") for req in spec.runtime_requirements}
if not req_families:
    sys.exit(0)

if "deepmd-jax" in req_families or "jax" in req_families:
    print("deepmd-jax")
elif "matclaw-cips" in req_families:
    print("matclaw-cips")
elif "ai2kit" in req_families or "004" in case_dir.name:
    print("提示: Case 004 (ai2kit) 依赖集群预置 SIF 镜像，无本地 Docker 构建目标", file=sys.stderr)
    sys.exit(0)
else:
    print(f"build.sh: {case_dir.name}: no matching local recipe for {req_families}", file=sys.stderr)
    sys.exit(1)
PY
}

expand() {
    local t
    for t in "$@"; do
        case "$t" in
            all)
                echo matclaw-cips
                echo deepmd-jax
                ;;
            matclaw-cips|ccbench-matclaw-cips|dftworld-base-matclaw-cips)
                echo matclaw-cips
                ;;
            deepmd-jax|ccbench-deepmd-jax|dftworld-base-deepmd-jax)
                echo deepmd-jax
                ;;
            jax-gpu)
                echo "提示: jax-gpu 为 CompShare 云端镜像配方，请运行 scripts/qualification/build_compshare_image_b.py" >&2
                ;;
            004|004-ai2kit-water64-end-to-end-potential)
                echo "提示: Case 004 依赖集群预置 SIF 镜像 (ai2kit/cp2k)，无本地 Docker 构建目标" >&2
                ;;
            *)
                local steps
                if steps="$(resolve_task_steps "$t" 2>/dev/null)"; then
                    if [ -n "$steps" ]; then
                        expand $steps
                    fi
                else
                    echo "未知目标或案例: $t" >&2
                    return 1
                fi
                ;;
        esac
    done
}

STEPS=()
while IFS= read -r step; do
    [ -z "$step" ] && continue
    skip=false
    for s in "${STEPS[@]+"${STEPS[@]}"}"; do
        if [ "$s" = "$step" ]; then skip=true; break; fi
    done
    if [ "$skip" = false ]; then
        STEPS+=("$step")
    fi
done < <(expand "${TARGETS[@]}")

if [ ${#STEPS[@]} -eq 0 ]; then
    for arg in "${TARGETS[@]}"; do
        case "$arg" in
            004|004-ai2kit-water64-end-to-end-potential|jax-gpu)
                exit 0
                ;;
        esac
    done
    echo "没有可构建的步骤" >&2
    exit 1
fi

echo "=== 将构建: ${STEPS[*]} ==="
docker images 2>/dev/null | grep -E "ccbench|mlffbench|dftworld|REPOSITORY" || true
echo ""

rmi_if_force() {
    local tag="$1"
    if [ "$FORCE" = true ]; then
        docker rmi -f "$tag" 2>/dev/null || true
    fi
}

for step in "${STEPS[@]}"; do
    case "$step" in
        matclaw-cips)
            rmi_if_force ccbench-matclaw-cips:cpu
            rmi_if_force dftworld-base-matclaw-cips:2.2.11-cpu
            echo "=== Building ccbench-matclaw-cips:cpu ==="
            docker build \
                --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
                -f "$DIR/matclaw-cips/Dockerfile" \
                -t ccbench-matclaw-cips:cpu \
                -t ccbench-matclaw-cips:latest \
                -t dftworld-base-matclaw-cips:2.2.11-cpu \
                -t dftworld-base-matclaw-cips:latest \
                "$ROOT"
            ;;
        deepmd-jax)
            rmi_if_force ccbench-deepmd-jax:cpu
            rmi_if_force dftworld-base-deepmd-jax:0.1.0-cpu
            echo "=== Building ccbench-deepmd-jax:cpu ==="
            docker build \
                -f "$DIR/deepmd-jax/Dockerfile" \
                -t ccbench-deepmd-jax:cpu \
                -t ccbench-deepmd-jax:latest \
                -t dftworld-base-deepmd-jax:0.1.0-cpu \
                -t dftworld-base-deepmd-jax:latest \
                "$DIR/deepmd-jax"
            ;;
        *)
            echo "internal error: unknown step $step" >&2
            exit 1
            ;;
    esac
    echo ""
done

echo "=== Done ==="
