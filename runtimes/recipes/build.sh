#!/bin/bash
# Bench 运行环境与候选沙箱镜像构建脚本
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
按需构建 Bench 隐藏科学 verifier 镜像。

真实可用目标 (Active Targets):
  matclaw-cips            bench-runtime-deepmd-kit:2.2.11-cpu
  deepmd-jax              bench-runtime-deepmd-jax:0.2-cpu
  all                     构建以上全部 verifier 镜像

注意:
  - jax-gpu 为 CompShare GPU 镜像配方 (由 recipe.lock.json 定义)，
    如需构建请使用 scripts/qualification/build_compshare_image_b.py。
  - Case 004 (AI2Kit+CP2K) 依赖集群预置 Singularity/SIF 镜像，由 HPC 控制层直接调度，
    无本地 Docker 构建目标；Candidate 仍在 Candidate Docker 中运行，远端计算由 Operator 负责。

也可传入案例目录名或编号（如 001 或 001-matclaw-cips-active-distillation），自动解析依赖。

示例:
  BENCH_UV_IMAGE=ghcr.io/astral-sh/uv:0.8.14@sha256:<verified-uv-digest> \\
    bash build.sh matclaw-cips
  bash build.sh 001
EOF
}

BASE_IMAGE="${BENCH_BASE_IMAGE:-ubuntu:24.04}"
UV_IMAGE="${BENCH_UV_IMAGE:-}"
PYTHON_IMAGE="${BENCH_PYTHON_IMAGE:-}"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="${BENCH_PYTHON:-python3}"

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
    echo "=== 当前 Bench 镜像 ==="
    docker images 2>/dev/null | grep -E "bench|bench|dftworld|REPOSITORY" || echo "(未连接 Docker 或无镜像)"
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
from bench.contracts.case import CaseSpec

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
            matclaw-cips|bench-matclaw-cips|dftworld-base-matclaw-cips)
                echo matclaw-cips
                ;;
            deepmd-jax|bench-deepmd-jax|dftworld-base-deepmd-jax)
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
docker images 2>/dev/null | grep -E "bench|bench|dftworld|REPOSITORY" || true
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
            if [[ ! "$BASE_IMAGE" =~ ^.+@sha256:[0-9a-f]{64}$ ]]; then
                echo "matclaw-cips requires BENCH_BASE_IMAGE=<registry/image:version>@sha256:<64 lowercase hex>" >&2
                exit 2
            fi
            if [[ ! "$UV_IMAGE" =~ ^.+@sha256:[0-9a-f]{64}$ ]]; then
                echo "matclaw-cips requires BENCH_UV_IMAGE=<registry/image:version>@sha256:<64 lowercase hex>" >&2
                echo "Resolve and inspect the uv image first; do not build from a mutable uv tag." >&2
                exit 2
            fi
            if [[ ! "$PYTHON_IMAGE" =~ ^.+@sha256:[0-9a-f]{64}$ ]]; then
                echo "matclaw-cips requires BENCH_PYTHON_IMAGE=<registry/image:version>@sha256:<64 lowercase hex>" >&2
                exit 2
            fi
            rmi_if_force bench-runtime-deepmd-kit:2.2.11-cpu
            echo "=== Building bench-runtime-deepmd-kit:2.2.11-cpu ==="
            docker build \
                --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
                --build-arg "UV_IMAGE=${UV_IMAGE}" \
                --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}" \
                -f "$DIR/matclaw-cips/Dockerfile" \
                -t bench-runtime-deepmd-kit:2.2.11-cpu \
                "$ROOT"
            ;;
        deepmd-jax)
            image_tag="bench-runtime-deepmd-jax:0.2-cpu"
            if [ "$FORCE" = false ] && docker image inspect "$image_tag" >/dev/null 2>&1; then
                echo "=== Reusing existing $image_tag ==="
                continue
            fi
            rmi_if_force "$image_tag"
            echo "=== Building $image_tag ==="
            for asset in \
                "$DIR/deepmd-jax/assets/deepmd_jax-0.2.tar.gz:f6a4de451d24ef1d540b6935b5ad56e65af860401a1e03247426a02d60cdba15" \
                "$DIR/deepmd-jax/assets/jax_md-0.2.29.tar.gz:6d05f6e17c47e0c545d78d35c9fc7c86dbbc0c7e127206abff0a16101f0c12ce"; do
                path="${asset%:*}"
                expected="${asset##*:}"
                actual="$(shasum -a 256 "$path" | awk '{print $1}')"
                [ "$actual" = "$expected" ] || { echo "asset hash mismatch: $path" >&2; exit 2; }
            done
            docker build \
                -f "$DIR/deepmd-jax/Dockerfile" \
                -t "$image_tag" \
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
