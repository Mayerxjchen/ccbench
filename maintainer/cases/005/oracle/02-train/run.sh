#!/bin/bash
# 042 stage 2 — initial DPMP training.
set -euo pipefail
cd "$(dirname "$0")"
source ../env.sh
IFACE="${1:-graphene-water}"

if [ "$AI2KIT_042_PROFILE" = "formal" ]; then STEPS=1000000; else STEPS=100; fi
SEED="${AI2KIT_042_SEED:-$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')}"

mkdir -p ../work

# Copy labeled data from01-aimd
LABELED="../01-aimd/work/$IFACE/labeled"
if [ ! -d "$LABELED" ]; then
  echo "[02-train] ERROR: labeled data not found: $LABELED" >&2
  echo "  Run 01-aimd first." >&2
  exit 1
fi
rm -rf "../work/train-round0"
cp -r "$LABELED" ../work/train-round0

"$PYTHON" train.py --steps "$STEPS" --data-dir ../work/train-round0 \
  --seed "$SEED" --interface "$IFACE"
echo "[02-train] done: model.pkl ($STEPS steps, interface=$IFACE)"
