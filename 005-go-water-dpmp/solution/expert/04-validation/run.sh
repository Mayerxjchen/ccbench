#!/bin/bash
# 042 stage 4 — scientific validation: E/F accuracy, NVT stability, water
# density profile across the interface.
set -euo pipefail
cd "$(dirname "$0")"
source ../env.sh
IFACE="${1:-graphene-water}"

if [ "$AI2KIT_042_PROFILE" = "formal" ]; then NVT_STEPS=5000; else NVT_STEPS=200; fi

# Find the latest training round
LATEST_ROUND=0
for d in ../work/train-round*; do
  if [ -d "$d" ]; then
    rnd=${d##*-round}
    if [ "$rnd" -gt "$LATEST_ROUND" ] 2>/dev/null; then
      LATEST_ROUND=$rnd
    fi
  fi
done
echo "[04] using train-round$LATEST_ROUND"

# Read structure
STRUCT_DIR="../structures/$IFACE"
if [ ! -d "$STRUCT_DIR" ]; then
  echo "[04-validation] ERROR: structure not found: $STRUCT_DIR" >&2
  exit 1
fi

mkdir -p ../work/validation

"$PYTHON" - "$NVT_STEPS" "$STRUCT_DIR" "$IFACE" "$LATEST_ROUND" <<'PY'
import sys
from pathlib import Path
import numpy as np
from deepmd_jax.train import test as dmj_test
from deepmd_jax.md import Simulation

nvt_steps, struct_dir, iface, latest_round = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
struct = Path(struct_dir)
model = f"../02-train/model.pkl"
train_dir = f"../work/train-round{latest_round}"

# 1. E/F accuracy on the agent's own held-out labels
rmse, pred, gt = dmj_test(model, train_dir, batch_size=16)
print(f"[04] E/F RMSE: {rmse}")

# 2. NVT stability (300 K)
box = np.loadtxt(struct / "box.raw").reshape(3, 3)
typ = np.loadtxt(struct / "type.raw", dtype=int).reshape(-1)
coord = np.loadtxt(struct / "coord.raw").reshape(-1, 3)
sim = Simulation(model_path=model, box=box, type_idx=typ,
                 mass=[15.999, 1.008, 12.011], routine="NVT", dt=0.5,
                 initial_position=coord, temperature=300, seed=7)
traj = sim.run(nvt_steps)
pos = np.asarray(traj["position"]).reshape(-1, -1, 3)
if not np.all(np.isfinite(pos)):
    raise SystemExit("[04] NVT produced NaN")
print(f"[04] NVT stable: {pos.shape[0]} frames, no NaN")

# 3. water oxygen density profile (graphene-water interface only)
if "graphene" in iface:
    lz = float(box[2, 2])
    c_mask = typ == 2
    o_mask = typ == 0
    if np.any(c_mask):
        plane_z = float(np.mean(pos[:, c_mask, 2] % lz))
        dz = pos[:, o_mask, 2] - plane_z
        dz = dz - lz * np.round(dz / lz)
        dist = np.abs(dz)
        hist, edges = np.histogram(dist, bins=np.arange(0, lz / 2 + 0.1, 0.1))
        centers = 0.5 * (edges[:-1] + edges[1:])
        peak_idx = int(np.argmax(hist[20:])) + 20 if len(hist) > 20 else 0
        peak = centers[peak_idx]
        print(f"[04] first O peak ~{peak:.2f} A from graphite plane")
        with open("../work/validation/density.txt", "w") as fh:
            for c, h in zip(centers, hist):
                fh.write(f"{c:.2f} {h}\n")
    else:
        print(f"[04] no carbon atoms in {iface}, skipping density profile")
else:
    print(f"[04] {iface} is not a graphene interface, skipping density profile")

np.save("../work/validation/nvt_positions.npy", pos)
PY

echo "[04-validation] done: interface=$IFACE"
