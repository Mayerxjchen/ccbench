#!/bin/bash
# Stage 033 public/ to the cluster and submit the two paper-profile formal
# re-runs (seeds 2026081301, 2026081305) — 033 byte restoration. The original
# workspace bytes were never preserved; re-running the frozen solution with the
# original seeds rebuilds benchmark-valid v2 evidence.
#
# Usage (run from the repo root, host has ssh <site-alias>):
#   scripts/reference/submit_033_formal.sh          # dry-run summary
#   scripts/reference/submit_033_formal.sh --submit # actually submit
#
# This is a hard-to-reverse, resource-heavy action (2 GPU paper runs on the
# production cluster).  Default is a dry run; pass --submit explicitly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE=/public/home/<site-user>/dftworld2-runs/matclaw-033
PUBLIC_SRC="$ROOT/033-matclaw-cips-domain-wall-search/public"
SLURM="$ROOT/scripts/reference/paper_033.slurm"
SEEDS=(2026081301 2026081305)
SUBMIT=false
[ "${1:-}" = "--submit" ] && SUBMIT=true

test -d "$PUBLIC_SRC" || { echo "public source missing: $PUBLIC_SRC"; exit 1; }
test -f "$SLURM" || { echo "slurm template missing: $SLURM"; exit 1; }

for seed in "${SEEDS[@]}"; do
  RUN_DIR="$BASE/runs/formal-${seed}/workspace"
  LOG_DIR="$BASE/runs/formal-${seed}"
  echo "== seed $seed -> $RUN_DIR"
  if $SUBMIT; then
    ssh -o BatchMode=yes <site-alias> "mkdir -p '$RUN_DIR' '$LOG_DIR' && rm -rf '$RUN_DIR'/*"
    rsync -a "$PUBLIC_SRC/." "<site-alias>:$RUN_DIR/"
    # --job-name/--output/--error are seed-specific sbatch flags because
    # #SBATCH lines inside the script are not shell-expanded.
    cat "$SLURM" | ssh -o BatchMode=yes <site-alias> \
      "SEED=$seed BASE=$BASE sbatch \
       --job-name=matclaw-033-paper-${seed} \
       --output='$LOG_DIR/slurm-%j.out' --error='$LOG_DIR/slurm-%j.err'" || true
  else
    echo "   (dry run) would stage public + submit paper job seed=$seed"
  fi
done
