#!/bin/bash
#SBATCH --job-name=matclaw-033-paper
#SBATCH --account=acct-blocked
#SBATCH --partition=gpu
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=12:00:00
# NOTE: --job-name / --output / --error are passed as sbatch flags by the
# submit orchestrator (seed-specific), not set here — #SBATCH lines are NOT
# shell-expanded, so a literal ${SEED} in them breaks the %x output path.
# Case 033 paper-profile formal re-run (byte restoration).
#
# The original 033 formal workspace bytes were never preserved (v1 manifests
# carried only per-artifact sha256, no bundle). Re-running the frozen
# solution+profile with the original seeds (2026081301/2026081305) is the only
# honest way to rebuild benchmark-valid v2 evidence. Faithful to paper_032:
# apptainer GPU SIF, SIF sha locked, arch + GPU gates, solution mounted
# read-only, public inputs pre-staged into the fresh run workspace.
#
# Env: SEED (paper seed, e.g. 2026081301) must be set at submit time.
set -euo pipefail

BASE=/public/home/<site-user>/dftworld2-runs/matclaw-033
RUN_DIR=$BASE/runs/formal-${SEED}/workspace
SOLUTION_DIR=$BASE/solution
SIF=$BASE/runtime/matclaw-cips-2.2.11-gpu-amd64.sif
LOCKED_SIF_SHA=99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3
APPTAINER=/public/software/apptainer/bin/apptainer

fail_infra() {
  echo "infrastructure_failed: $*"
  exit 1
}

echo "seed=$SEED node=$(hostname) run_dir=$RUN_DIR"

[ "$(uname -m)" = "x86_64" ] || fail_infra "arch $(uname -m)"
[ -f "$SIF" ] || fail_infra "SIF missing"
SIF_SHA=$(shasum -a 256 "$SIF" | cut -d' ' -f1)
[ "$SIF_SHA" = "$LOCKED_SIF_SHA" ] || fail_infra "SIF sha $SIF_SHA != locked"
[ -d "$RUN_DIR" ] || fail_infra "run dir missing (stage public first)"
[ -x "$SOLUTION_DIR/solve.sh" ] || fail_infra "solution missing"
if ! command -v nvidia-smi >/dev/null 2>&1; then fail_infra "no GPU"; fi
GPU_LINE=$(nvidia-smi -L 2>/dev/null | head -1)
echo "infra_gates=pass sif_sha=$SIF_SHA gpu=$GPU_LINE"

exec "$APPTAINER" exec --nv --containall \
  --env MATCLAW_PROFILE='paper' --env MATCLAW_SEED="${SEED}" --env MATCLAW_OUTPUT='/app' \
  --bind "$RUN_DIR:/app" --bind "$SOLUTION_DIR:/solution:ro" \
  "$SIF" /solution/solve.sh
