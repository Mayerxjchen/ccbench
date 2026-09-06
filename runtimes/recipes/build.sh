#!/bin/bash
# 按需构建 dftworld 基础镜像，避免一次打满磁盘。
#
# 用法:
#   bash build.sh                  # 只列当前镜像 + 帮助
#   bash build.sh base             # 基础镜像
#   bash build.sh cp2k chem        # 指定若干 target
#   bash build.sh 031-matclaw-cips-active-distillation # 按任务 Dockerfile FROM 构建
#   bash build.sh -f base          # 先删再重建
#   bash build.sh all              # 全量（占磁盘，慎用）
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
FORCE=false

usage() {
    cat <<'EOF'
按需构建 dftworld 基础镜像。

Targets:
  base         dftworld-base              (hello / version / pip)
  cp2k         dftworld-base-cp2k         (依赖 base 的替代：直接 FROM cp2k 官方镜像)
  chem         dftworld-base-chem         (ase / rdkit，依赖 base)
  deepmd       dftworld-base-deepmd       (依赖 base)
  deepmd-jax   dftworld-base-deepmd-jax   (DPMP/JAX: jax+flax+optax+jax-md+deepmd-jax，依赖 ai2kit)
  packmol-src  dftworld-base-packmol-src  (依赖 base)
  packmol      dftworld-base-packmol      (依赖 packmol-src)
  xtb  dftworld-base-xtb   (ase3.25/rdkit/tblite/pymatgen，依赖 base)
  mace dftworld-base-mace  (ase3.25/rdkit/mace-torch，依赖 base)
  matclaw-cips dftworld-base-matclaw-cips (DeePMD 2.2.11 + LAMMPS + CIPS teacher)
  matclaw-cips-gpu dftworld-base-matclaw-cips:2.2.11-gpu (DeePMD 2.2.11 + CUDA 12 + qualify_gpu)
  matclaw-cips-controller dftworld-base-matclaw-cips:2.2.11-controller (HPC 控制层，依赖 matclaw-cips)
  ai2kit-controller dftworld-base-ai2kit:0.1.0-cpu-controller (HPC 控制层，依赖外部 ai2kit base)
  candidate-claude-code mlffbench-candidate-claude-code-sandbox:v1 (独立 Candidate Agent Sandbox 镜像)
  skills       dftworld-skills:<sha>      (skill bundle，非执行镜像；生成 .skill-image.json)
  all          以上全部（很吃磁盘）

也可直接传任务名（如 031-matclaw-cips-active-distillation），会读 Dockerfile 的 FROM。

示例:
  bash build.sh base
  bash build.sh 031-matclaw-cips-active-distillation
  bash build.sh cp2k
  bash build.sh -f chem

Docker Hub 超时（国内常见）时换基础镜像，任选其一:
  # 镜像站
  DFTWORLD_BASE_IMAGE=docker.m.daocloud.io/library/ubuntu:24.04 bash build.sh base
  # 本机已有 debian 也可（你机器上已有 bookworm-slim）
  DFTWORLD_BASE_IMAGE=debian:bookworm-slim bash build.sh base
EOF
}

# 拉取 ubuntu 超时可覆盖，例如:
#   DFTWORLD_BASE_IMAGE=docker.m.daocloud.io/library/ubuntu:24.04
#   DFTWORLD_BASE_IMAGE=debian:bookworm-slim
BASE_IMAGE="${CCBENCH_BASE_IMAGE:-${DFTWORLD_BASE_IMAGE:-ubuntu:24.04}}"

# 运行时注册表解析用同一套 Python（runtime registry 在 dftworld_bench 内）。
PY="${CCBENCH_PYTHON:-${DFTWORLD_PYTHON:-$ROOT/.venv/bin/python}}"
[ -x "$PY" ] || PY="${CCBENCH_PYTHON:-${DFTWORLD_PYTHON:-python3}}"

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
    echo "=== 当前 dftworld 镜像 ==="
    docker images | grep -E "dftworld|REPOSITORY" || echo "(无)"
    echo ""
    usage
    exit 0
fi

# 目录 -> 其 Dockerfile：优先根布局 <dir>/Dockerfile，兼容旧
# environment/ 布局 <dir>/environment/Dockerfile。
resolve_dockerfile() {
    local dir="$1"
    local df="$dir/Dockerfile"
    [ -f "$df" ] || df="$dir/environment/Dockerfile"
    if [ ! -f "$df" ]; then
        echo "no Dockerfile (root or environment/) under $dir" >&2
        return 1
    fi
    echo "$df"
}

# target -> docker tag / build context 名
resolve_task_image() {
    local task="$1"
    local df
    df="$(resolve_dockerfile "$ROOT/$task")" || return 1
    awk '/^FROM / {print $2; exit}' "$df"
}

# case 名 -> 经运行时注册表校验的 build step（无依赖 -> base）。
# 迁移后的 case 不再在 manifest 里写死 Infra 镜像：构建目标由 task.toml 的
# [runtime] requirements 经 dftworld_bench.runtime.registry 解析。注册表拒绝
# （零匹配 / 歧义 / 冲突 / 未锁定 digest）时 fail closed——绝不猜镜像。直到
# 授权镜像门（qualify_runtimes.py）锁定 digest，遗留 family（cp2k / packmol /
# deepmd-kit / ase-rdkit / mace / xtb）都不会被隐式构建。
resolve_task_steps() {
    local task="$1"
    [ -n "$task" ] || return 1
    "$PY" - "$ROOT/$task" <<'PY' || return 1
import sys
from pathlib import Path

from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.runtime.registry import (
    RuntimeRegistry,
    RuntimeRegistryError,
    RuntimeRequirement,
)

# registry compute family -> build.sh target 名
_STEP_BY_FAMILY = {
    "deepmd-jax": "deepmd-jax",
    "matclaw-cips": "matclaw-cips",
}

case_dir = Path(sys.argv[1])
spec = CaseSpec.load(case_dir)
requirements = [
    RuntimeRequirement(
        name=req.name,
        family=req.implementation or req.name,
        version=req.version or "*",
    )
    for req in spec.runtime_requirements
]
if not requirements:
    # 无 compute 依赖:local candidate 镜像即 dftworld-base
    print("base")
    sys.exit(0)
try:
    resolved = RuntimeRegistry().resolve(requirements, spec.execution_class)
except RuntimeRegistryError as exc:
    print(f"build.sh: {case_dir.name}: {exc}", file=sys.stderr)
    print(
        "build.sh: no qualified compute runtime for this case; deferred to the "
        "authorized image gate. Nothing was built.",
        file=sys.stderr,
    )
    sys.exit(1)
if resolved.compute.digest is None:
    print(
        f"build.sh: {case_dir.name}: resolved compute runtime "
        f"{resolved.compute.image!r} has no locked digest; deferred to the "
        "authorized image gate. Nothing was built.",
        file=sys.stderr,
    )
    sys.exit(1)
step = _STEP_BY_FAMILY.get(resolved.compute.family)
if step is None:
    print(
        f"build.sh: {case_dir.name}: no build target for registry family "
        f"{resolved.compute.family!r}",
        file=sys.stderr,
    )
    sys.exit(1)
print(step)
PY
}

# 展开为有序、去重的构建步骤（含依赖）
expand() {
    local t
    for t in "$@"; do
        case "$t" in
            all)
                echo base
                echo cp2k
                echo packmol-src
                echo packmol
                echo chem
                echo deepmd
                echo deepmd-jax
                echo xtb
                echo mace
                echo matclaw-cips
                echo matclaw-cips-controller
                echo ai2kit-controller
                echo skills
                ;;
            base) echo base ;;
            cp2k) echo cp2k ;;
            chem) echo base; echo chem ;;
            deepmd) echo base; echo deepmd ;;
            deepmd-jax) echo deepmd-jax ;;
            packmol-src) echo base; echo packmol-src ;;
            packmol) echo base; echo packmol-src; echo packmol ;;
            xtb) echo base; echo xtb ;;
            mace) echo base; echo mace ;;
            matclaw-cips) echo base; echo matclaw-cips ;;
            matclaw-cips-gpu) echo base; echo matclaw-cips; echo matclaw-cips-gpu ;;
            matclaw-cips-controller) echo base; echo matclaw-cips; echo matclaw-cips-controller ;;
            ai2kit-controller) echo ai2kit-controller ;;
            skills) echo skills ;;
            dftworld-base) echo base ;;
            dftworld-base-cp2k) echo cp2k ;;
            dftworld-base-chem) echo base; echo chem ;;
            dftworld-base-deepmd) echo base; echo deepmd ;;
            dftworld-base-deepmd-jax) echo deepmd-jax ;;
            dftworld-base-packmol-src) echo base; echo packmol-src ;;
            dftworld-base-packmol) echo base; echo packmol-src; echo packmol ;;
            dftworld-base-xtb) echo base; echo xtb ;;
            dftworld-base-mace) echo base; echo mace ;;
            dftworld-base-matclaw-cips) echo base; echo matclaw-cips ;;
            dftworld-base-matclaw-cips:2.2.11-gpu) echo base; echo matclaw-cips; echo matclaw-cips-gpu ;;
            dftworld-base-matclaw-cips:2.2.11-controller) echo base; echo matclaw-cips; echo matclaw-cips-controller ;;
            dftworld-base-ai2kit:0.1.0-cpu-controller) echo ai2kit-controller ;;
            dftworld-skills) echo skills ;;
            *)
                # case 目录名:registry 解析;失败时 fail closed(见 resolve_task_steps)
                local steps
                steps="$(resolve_task_steps "$t")" || return 1
                expand $steps
                ;;
        esac
    done
}

STEPS=()
while IFS= read -r step; do
    [ -z "$step" ] && continue
    # 去重保序
    skip=false
    for s in "${STEPS[@]+"${STEPS[@]}"}"; do
        if [ "$s" = "$step" ]; then skip=true; break; fi
    done
    if [ "$skip" = false ]; then
        STEPS+=("$step")
    fi
done < <(expand "${TARGETS[@]}")

if [ ${#STEPS[@]} -eq 0 ]; then
    echo "没有可构建的步骤" >&2
    exit 1
fi

echo "=== 将构建: ${STEPS[*]} ==="
docker images | grep -E "dftworld|REPOSITORY" || true
echo ""

rmi_if_force() {
    local tag="$1"
    if [ "$FORCE" = true ]; then
        docker rmi -f "$tag" 2>/dev/null || true
    fi
}

for step in "${STEPS[@]}"; do
    case "$step" in
        base)
            rmi_if_force dftworld-base
            echo "=== Building dftworld-base (FROM ${BASE_IMAGE}) ==="
            docker build \
                --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
                -t dftworld-base \
                "$DIR/base"
            ;;
        cp2k)
            rmi_if_force dftworld-base-cp2k
            echo "=== Building dftworld-base-cp2k ==="
            docker build -t dftworld-base-cp2k "$DIR/cp2k"
            ;;
        packmol-src)
            rmi_if_force dftworld-base-packmol-src
            echo "=== Building dftworld-base-packmol-src ==="
            docker build -t dftworld-base-packmol-src "$DIR/packmol-src"
            ;;
        packmol)
            rmi_if_force dftworld-base-packmol
            echo "=== Building dftworld-base-packmol ==="
            docker build -t dftworld-base-packmol "$DIR/packmol"
            ;;
        chem)
            rmi_if_force dftworld-base-chem
            echo "=== Building dftworld-base-chem ==="
            docker build -t dftworld-base-chem "$DIR/chem"
            ;;
        deepmd)
            rmi_if_force dftworld-base-deepmd
            echo "=== Building dftworld-base-deepmd ==="
            docker build -t dftworld-base-deepmd "$DIR/deepmd"
            ;;
        deepmd-jax)
            rmi_if_force dftworld-base-deepmd-jax:0.1.0-cpu
            rmi_if_force dftworld-base-deepmd-jax
            echo "=== Building dftworld-base-deepmd-jax:0.1.0-cpu ==="
            docker build \
                -t dftworld-base-deepmd-jax:0.1.0-cpu \
                -t dftworld-base-deepmd-jax \
                "$DIR/deepmd-jax"
            ;;
        xtb)
            rmi_if_force dftworld-base-xtb
            echo "=== Building dftworld-base-xtb ==="
            docker build -t dftworld-base-xtb "$DIR/xtb"
            ;;
        mace)
            rmi_if_force dftworld-base-mace
            echo "=== Building dftworld-base-mace ==="
            docker build -t dftworld-base-mace "$DIR/mace"
            ;;
        matclaw-cips)
            rmi_if_force dftworld-base-matclaw-cips
            echo "=== Building dftworld-base-matclaw-cips ==="
            docker build \
                -f "$DIR/matclaw-cips/Dockerfile" \
                -t dftworld-base-matclaw-cips:2.2.11-cpu \
                -t dftworld-base-matclaw-cips \
                "$ROOT"
            ;;
        matclaw-cips-gpu)
            rmi_if_force dftworld-base-matclaw-cips:2.2.11-gpu
            echo "=== Building dftworld-base-matclaw-cips:2.2.11-gpu ==="
            docker build \
                -f "$DIR/matclaw-cips-gpu/Dockerfile" \
                -t dftworld-base-matclaw-cips:2.2.11-gpu \
                "$ROOT"
            ;;
        matclaw-cips-controller)
            rmi_if_force dftworld-base-matclaw-cips:2.2.11-controller
            echo "=== Building dftworld-base-matclaw-cips:2.2.11-controller ==="
            docker build \
                -f "$DIR/matclaw-cips-controller/Dockerfile" \
                -t dftworld-base-matclaw-cips:2.2.11-controller \
                "$ROOT"
            ;;
        ai2kit-controller)
            rmi_if_force dftworld-base-ai2kit:0.1.0-cpu-controller
            echo "=== Building dftworld-base-ai2kit:0.1.0-cpu-controller ==="
            docker build \
                -f "$DIR/ai2kit-controller/Dockerfile" \
                -t dftworld-base-ai2kit:0.1.0-cpu-controller \
                "$ROOT"
            ;;
        candidate-claude-code|agent-claude-code)
            rmi_if_force mlffbench-candidate-claude-code-sandbox:v1
            echo "=== Building mlffbench-candidate-claude-code-sandbox:v1 ==="
            docker build \
                -f "$DIR/agent-claude-code/Dockerfile" \
                -t mlffbench-candidate-claude-code-sandbox:v1 \
                "$DIR/agent-claude-code"
            ;;
        skills)
            # 唯一依赖 git:取当前 HEAD 短 hash 作为 immutable tag
            SHA="$(git -C "$ROOT" rev-parse --short=10 HEAD 2>/dev/null || echo unknown)"
            rmi_if_force dftworld-skills:latest
            echo "=== Building dftworld-skills:$SHA (skills bundle) ==="
            docker build \
                -f "$DIR/skills.Dockerfile" \
                -t dftworld-skills:latest \
                -t "dftworld-skills:$SHA" \
                "$DIR"
            # 生成 manifest:tag/commit/skills_sha,供 eval 锁定身份
            python3 "$ROOT/skills_sha.py" \
                --dir "$DIR/skills" \
                --write "$DIR/.skill-image.json" \
                --tag "dftworld-skills:$SHA" \
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
docker images | grep dftworld-base || true
