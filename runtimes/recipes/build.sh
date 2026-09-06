#!/bin/bash
# CCBench 运行环境与候选沙箱镜像构建脚本
#
# 用法:
#   bash build.sh                          # 列出可用目标与当前镜像
#   bash build.sh agent-claude-code        # 构建 Candidate Agent Sandbox
#   bash build.sh matclaw-cips             # 构建 MatClaw CIPS CPU 镜像
#   bash build.sh matclaw-cips-gpu         # 构建 MatClaw CIPS GPU 镜像
#   bash build.sh 001                      # 解析用例依赖并构建
#   bash build.sh -f skills                # 强制重新构建 skills 镜像
#   bash build.sh all                      # 构建所有真实存在的本地 Docker 镜像
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
FORCE=false

usage() {
    cat <<'EOF'
按需构建 CCBench 运行环境与候选沙箱镜像。

真实可用目标 (Active Targets):
  agent-claude-code       ccbench-agent-claude-code:v1 (Candidate Agent Sandbox 镜像)
  matclaw-cips            ccbench-matclaw-cips:cpu (DeePMD + LAMMPS + CIPS teacher)
  matclaw-cips-gpu        ccbench-matclaw-cips:gpu (DeePMD + CUDA 12 + qualify_gpu，自动依赖 matclaw-cips)
  matclaw-cips-controller ccbench-matclaw-cips:controller (HPC 控制层沙箱)
  ai2kit-controller       ccbench-ai2kit:controller (Case 004 AI2Kit+CP2K HPC 控制层沙箱，调度集群 SIF 任务)
  deepmd-jax              ccbench-deepmd-jax:cpu (DP-MP / JAX runtime)
  skills                  ccbench-skills:<sha> (技能包镜像，生成 .skill-image.json)
  all                     构建以上全部本地 Dockerfile 镜像

注意:
  - jax-gpu 为 CompShare GPU 镜像配方 (由 recipe.lock.json 定义)，
    如需构建请使用 scripts/qualification/build_compshare_image_b.py。
  - Case 004 (AI2Kit+CP2K) 依赖集群预置 Singularity/SIF 镜像，由 HPC 控制层直接调度，
    无本地 Docker 计算引擎构建目标。如需构建控制层沙箱请指定 ai2kit-controller。

也可传入案例目录名或编号（如 001 或 001-matclaw-cips-active-distillation），自动解析依赖。

示例:
  bash build.sh agent-claude-code
  bash build.sh matclaw-cips
  bash build.sh 001
  bash build.sh -f skills
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
                echo agent-claude-code
                echo matclaw-cips
                echo matclaw-cips-gpu
                echo matclaw-cips-controller
                echo ai2kit-controller
                echo deepmd-jax
                echo skills
                ;;
            candidate-claude-code|agent-claude-code|mlffbench-candidate-claude-code-sandbox:v1|ccbench-agent-claude-code:v1)
                echo agent-claude-code
                ;;
            matclaw-cips|ccbench-matclaw-cips|dftworld-base-matclaw-cips)
                echo matclaw-cips
                ;;
            matclaw-cips-gpu|ccbench-matclaw-cips:gpu|dftworld-base-matclaw-cips:2.2.11-gpu)
                echo matclaw-cips
                echo matclaw-cips-gpu
                ;;
            matclaw-cips-controller|ccbench-matclaw-cips:controller|dftworld-base-matclaw-cips:2.2.11-controller)
                echo matclaw-cips
                echo matclaw-cips-controller
                ;;
            ai2kit-controller|ccbench-ai2kit:controller|dftworld-base-ai2kit:0.1.0-cpu-controller)
                echo ai2kit-controller
                ;;
            deepmd-jax|ccbench-deepmd-jax|dftworld-base-deepmd-jax)
                echo deepmd-jax
                ;;
            skills|ccbench-skills|dftworld-skills)
                echo skills
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
        agent-claude-code)
            rmi_if_force ccbench-agent-claude-code:v1
            rmi_if_force mlffbench-candidate-claude-code-sandbox:v1
            echo "=== Building ccbench-agent-claude-code:v1 ==="
            docker build \
                -f "$DIR/agent-claude-code/Dockerfile" \
                -t ccbench-agent-claude-code:v1 \
                -t mlffbench-candidate-claude-code-sandbox:v1 \
                "$DIR/agent-claude-code"
            ;;
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
        matclaw-cips-gpu)
            rmi_if_force ccbench-matclaw-cips:gpu
            rmi_if_force dftworld-base-matclaw-cips:2.2.11-gpu
            echo "=== Building ccbench-matclaw-cips:gpu ==="
            docker build \
                -f "$DIR/matclaw-cips-gpu/Dockerfile" \
                -t ccbench-matclaw-cips:gpu \
                -t dftworld-base-matclaw-cips:2.2.11-gpu \
                "$ROOT"
            ;;
        matclaw-cips-controller)
            rmi_if_force ccbench-matclaw-cips:controller
            rmi_if_force dftworld-base-matclaw-cips:2.2.11-controller
            echo "=== Building ccbench-matclaw-cips:controller ==="
            docker build \
                -f "$DIR/matclaw-cips-controller/Dockerfile" \
                -t ccbench-matclaw-cips:controller \
                -t dftworld-base-matclaw-cips:2.2.11-controller \
                "$ROOT"
            ;;
        ai2kit-controller)
            rmi_if_force ccbench-ai2kit:controller
            rmi_if_force dftworld-base-ai2kit:0.1.0-cpu-controller
            echo "=== Building ccbench-ai2kit:controller ==="
            docker build \
                -f "$DIR/ai2kit-controller/Dockerfile" \
                -t ccbench-ai2kit:controller \
                -t dftworld-base-ai2kit:0.1.0-cpu-controller \
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
        skills)
            SHA="$(git -C "$ROOT" rev-parse --short=10 HEAD 2>/dev/null || echo unknown)"
            rmi_if_force ccbench-skills:latest
            rmi_if_force dftworld-skills:latest
            echo "=== Building ccbench-skills:$SHA (skills bundle) ==="
            docker build \
                -f "$DIR/skills.Dockerfile" \
                -t ccbench-skills:latest \
                -t "ccbench-skills:$SHA" \
                -t dftworld-skills:latest \
                -t "dftworld-skills:$SHA" \
                "$DIR"
            python3 "$ROOT/skills_sha.py" \
                --dir "$DIR/skills" \
                --write "$ROOT/runtimes/locks/.skill-image.json" \
                --tag "ccbench-skills:$SHA" \
                --commit "$SHA"
            ;;
        *)
            echo "internal error: unknown step $step" >&2
            exit 1
            ;;
    esac
    echo ""
done

echo "=== Done ==="
