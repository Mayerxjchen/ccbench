#!/bin/bash
# Case 031 MatClaw CIPS GPU batch entry point (source template).
#
# The restricted controller renders this file with RUN_DIR / SOLUTION_DIR /
# MATCLAW_SIF / MATCLAW_LOCKED_SIF_SHA / MATCLAW_ENV_FLAGS and the cluster
# profile's Slurm directives (account/partition/QoS/gres/cpus/mem/time), the
# apptainer path, and the expected node arch substituted.  Any placeholder left
# unresolved is rejected before sbatch.  Do not edit the #SBATCH directives in
# the checked-in template — the cluster profile (scripts/hpc/cluster_profile.toml)
# is the single site configuration.
#
# Infra gates run at script start: node arch vs the locked SIF's platform,
# locked SIF SHA, GPU visibility, and required paths.  ANY infra failure is
# recorded as infrastructure_failed and the scientific verifier never runs —
# an infra fault is not an agent/solution failure (transparent HPC layer).
#SBATCH --job-name=__SBATCH_JOB_NAME__
#SBATCH --account=__SBATCH_ACCOUNT__
#SBATCH --partition=__SBATCH_PARTITION__
#SBATCH --qos=__SBATCH_QOS__
#SBATCH --gres=__SBATCH_GRES__
#SBATCH --cpus-per-task=__SBATCH_CPUS__
#SBATCH --mem=__SBATCH_MEM__
#SBATCH --time=__SBATCH_TIME__
#SBATCH --output=slurm-%j.out
set -euo pipefail

RUN_DIR=__RUN_DIR__
SOLUTION_DIR=__SOLUTION_DIR__
MATCLAW_SIF=__MATCLAW_SIF__
MATCLAW_LOCKED_SIF_SHA=__MATCLAW_LOCKED_SIF_SHA__
# MATCLAW_PROFILE / MATCLAW_SEED / MATCLAW_OUTPUT etc. are injected here by
# the controller as apptainer `--env KEY='VALUE'` flags (empty for a plain
# smoke run).  Host `export` lines would be dropped by --containall/--cleanenv.
APPTAINER=__APPTAINER_PATH__

# ---- infra gates -----------------------------------------------------------
fail_infra() {
  echo "infrastructure_failed: $*"
  echo "infrastructure_failed: $*" > "$(dirname "$RUN_DIR")/INFRA_FAILED.txt"
  exit 1
}

[ "$(uname -m)" = "__EXPECTED_NODE_ARCH__" ] || fail_infra "node arch $(uname -m) != __EXPECTED_NODE_ARCH__ (locked SIF would not run here)"
[ -f "$MATCLAW_SIF" ] || fail_infra "SIF missing: $MATCLAW_SIF"
[ -n "$MATCLAW_LOCKED_SIF_SHA" ] || fail_infra "no locked SIF SHA rendered"
SIF_SHA=$(shasum -a 256 "$MATCLAW_SIF" | cut -d' ' -f1)
[ "$SIF_SHA" = "$MATCLAW_LOCKED_SIF_SHA" ] || fail_infra "SIF sha $SIF_SHA != locked $MATCLAW_LOCKED_SIF_SHA"
[ -d "$RUN_DIR" ] || fail_infra "run workspace missing: $RUN_DIR"
[ -x "$SOLUTION_DIR/solve.sh" ] || fail_infra "solution missing: $SOLUTION_DIR/solve.sh"
[ -x "$APPTAINER" ] || fail_infra "apptainer not found: $APPTAINER"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  fail_infra "nvidia-smi not found (no GPU runtime on node)"
fi
GPU_LINE=$(nvidia-smi -L 2>/dev/null | head -1) || fail_infra "nvidia-smi -L failed"
echo "infra_gates=pass node=$(hostname) arch=$(uname -m) sif_sha=$SIF_SHA gpu=$GPU_LINE"
echo "infra_gates=pass node=$(hostname) arch=$(uname -m) sif_sha=$SIF_SHA gpu=$GPU_LINE" > "$(dirname "$RUN_DIR")/INFRA_PASS.txt"

exec "$APPTAINER" exec --nv --containall \
  __MATCLAW_ENV_FLAGS__ \
  --bind "$RUN_DIR:/app" --bind "$SOLUTION_DIR:/solution:ro" \
  "$MATCLAW_SIF" /solution/solve.sh
