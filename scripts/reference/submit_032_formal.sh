#!/bin/bash
# Stage 032 public/ to the cluster and submit the two paper-profile formal
# re-runs (seeds 2026081206, 2026081213) — Task 14 evidence restore.
#
# Usage (run from the repo root, host has ssh <site-alias>):
#   scripts/reference/submit_032_formal.sh          # dry-run summary
#   scripts/reference/submit_032_formal.sh --submit # actually submit
#
# This is a hard-to-reverse, resource-heavy action (2 x >10 h GPU paper runs
# on the production cluster).  Default is a dry run; pass --submit explicitly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE=/public/home/<site-user>/dftworld2-runs/matclaw-032
PUBLIC_SRC="$ROOT/032-matclaw-cips-curie-temperature/public"
SLURM="$ROOT/scripts/reference/paper_032.slurm"
SEEDS=(2026081206 2026081213)
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
       --job-name=matclaw-032-paper-${seed} \
       --output='$LOG_DIR/slurm-%j.out' --error='$LOG_DIR/slurm-%j.err'" || true
  else
    echo "   (dry run) would stage public + submit paper job seed=$seed"
  fi
done
