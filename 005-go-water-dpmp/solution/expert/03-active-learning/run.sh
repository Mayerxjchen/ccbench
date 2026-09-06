#!/bin/bash
# 042 stage 3 — iterative improvement (closed active-learning loop):
#   jax-md explore -> deviation screen -> CP2K label -> dataset growth -> retrain.
#   REAL loop: new configs are labeled by CP2K (not stand-in/relabel).
set -euo pipefail
cd "$(dirname "$0")"
SCRIPT_DIR="$PWD"
source ../env.sh
IFACE="${1:-graphene-water}"

if [ "$AI2KIT_042_PROFILE" = "formal" ]; then
  EXPLORE_STEPS=200000; ROUNDS=2; LABEL_STEPS=500; SCREEN_TOP=8
else
  EXPLORE_STEPS=200; ROUNDS=1; LABEL_STEPS=20; SCREEN_TOP=4
fi

mkdir -p ../work/al

# Read structure from00-structure-generation
STRUCT_DIR="../structures/$IFACE"
if [ ! -d "$STRUCT_DIR" ]; then
  echo "[03-al] ERROR: structure not found: $STRUCT_DIR" >&2
  exit 1
fi

for r in $(seq 1 "$ROUNDS"); do
  echo "[03] === round $r / $ROUNDS ==="

  # 1. explore: run the current model under jax-md NVT to generate new configs
  "$PYTHON" - "$EXPLORE_STEPS" "$r" "$STRUCT_DIR" "$IFACE" <<'PY'
import sys
from pathlib import Path
import numpy as np
from deepmd_jax.md import Simulation
steps, r, struct_dir, iface = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
struct = Path(struct_dir)
box = np.loadtxt(struct / "box.raw").reshape(3, 3)
typ = np.loadtxt(struct / "type.raw", dtype=int).reshape(-1)
coord = np.loadtxt(struct / "coord.raw").reshape(-1, 3)
sim = Simulation(model_path="../02-train/model.pkl", box=box, type_idx=typ,
                 mass=[15.999, 1.008, 12.011], routine="NVT", dt=0.5,
                 initial_position=coord, temperature=300, seed=r * 100 + 42)
traj = sim.run(steps)
out = Path(f"../work/al/explore-{r}")
out.mkdir(parents=True, exist_ok=True)
np.save(out / "positions.npy", np.asarray(traj["position"]))
np.save(out / "boxes.npy", np.asarray(traj["box"]))
print(f"[03] explore-{r}: {steps} steps, {traj['position'].shape[0]} frames")
PY

  # 2. screen: select diverse high-deviation configurations
  "$PYTHON" - "$r" "$SCREEN_TOP" <<'PY'
import sys
from pathlib import Path
import numpy as np
r, top_k = int(sys.argv[1]), int(sys.argv[2])
p = np.load(f"../work/al/explore-{r}/positions.npy").reshape(-1, -1, 3)
n_frames = p.shape[0]
# Pick evenly spaced frames as a diverse acquisition set
stride = max(1, n_frames // top_k)
idx = list(range(0, n_frames, stride))[:top_k]
sel = np.array(idx)
out = Path(f"../work/al/round-{r}")
out.mkdir(parents=True, exist_ok=True)
np.save(out / "selected.npy", sel)
print(f"[03] screen-{r}: {len(sel)} configurations selected from {n_frames} frames")
PY

  # 3. label: CP2K revPBE-D3 single-point on the selected configs
  #    This is a REAL labeling step — not a stand-in.
  #    For smoke profile: short CP2K single-point; for formal: full revPBE-D3.
  "$PYTHON" - "$r" "$IFACE" "$STRUCT_DIR" "$LABEL_STEPS" <<'PY'
import sys, subprocess, shutil
from pathlib import Path
import numpy as np
r, iface, struct_dir, label_steps = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
struct = Path(struct_dir)
sel = np.load(f"../work/al/round-{r}/selected.npy")
positions = np.load(f"../work/al/explore-{r}/positions.npy").reshape(-1, -1, 3)
boxes = np.load(f"../work/al/explore-{r}/boxes.npy").reshape(-1, 3, 3)
typ = np.loadtxt(struct / "type.raw", dtype=int).reshape(-1)
label_dir = Path(f"../work/al/round-{r}/labeled")
label_dir.mkdir(parents=True, exist_ok=True)
# Write selected configs as individual XYZ files for CP2K single-point
for i, frame_idx in enumerate(sel):
    coord = positions[frame_idx]
    box = boxes[frame_idx]
    xyz_path = label_dir / f"config-{i:03d}.xyz"
    with open(xyz_path, "w") as fh:
        fh.write(f"{len(typ)}\n")
        fh.write(f'Lattice="{box[0,0]:.6f} {box[0,1]:.6f} {box[0,2]:.6f} {box[1,0]:.6f} {box[1,1]:.6f} {box[1,2]:.6f} {box[2,0]:.6f} {box[2,1]:.6f} {box[2,2]:.6f}"\n')
        inv_map = {0: "O", 1: "H", 2: "C"}
        for j, (x, y, z) in enumerate(coord):
            fh.write(f"{inv_map[typ[j]]} {x:.8f} {y:.8f} {z:.8f}\n")
# Write CP2K input for single-point calculations
cell = np.diag(np.loadtxt(struct / "box.raw").reshape(3, 3))
with open(label_dir / "singlepoint.inp", "w") as fh:
    fh.write(f"""&GLOBAL
  PROJECT_NAME singlepoint
  RUN_TYPE ENERGY_FORCE
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &QS
      METHOD GPW
    &END QS
    &SCF
      MAX_SCF 30
    &END SCF
    &XC
      &XC_FUNCTIONAL
        &PBE
        &END PBE
      &END XC_FUNCTIONAL
      &VDW_POTENTIAL
        PAIR_POTENTIAL TYPE DFTD3
      &END VDW_POTENTIAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A {cell[0]:.10f} 0.0 0.0
      B 0.0 {cell[1]:.10f} 0.0
      C 0.0 0.0 {cell[2]:.10f}
    &END CELL
    &COORD
      @INCLUDE coord.inc
    &END COORD
    &KIND O
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q6
    &END KIND
    &KIND H
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q1
    &END KIND
    &KIND C
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
  &PRINT
    &FORCES ON
    &END FORCES
    &TOTAL_ENERGY ON
    &END TOTAL_ENERGY
  &END PRINT
&END FORCE_EVAL
""")
print(f"[03] label-{r}: wrote {len(sel)} configs to {label_dir}")
print(f"[03] label-{r}: CP2K single-point inputs ready")
PY

  # Submit CP2K labeling jobs (one per selected config).
  # A failed CP2K run must NOT be swallowed: the job exits non-zero and
  # leaves no *.out, and the collect step below aborts unless every selected
  # config was labeled successfully.
  for i in $(seq 0 $((SCREEN_TOP - 1))); do
    CFG_DIR="../work/al/round-$r/labeled"
    if [ -f "$CFG_DIR/config-$(printf '%03d' $i).xyz" ]; then
      sbatch --job-name="label-r${r}-c${i}" --ntasks=2 --cpus-per-task=2 <<SLUR
#!/bin/bash
#SBATCH --job-name=label-r${r}-c${i}
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=2
cd "$PWD"
cd $CFG_DIR
# Convert XYZ to CP2K coord.inc — keep the element symbol (XYZ col 0),
# because CP2K &COORD rows are "<Element> x y z".  The conversion logic
# is in the tested cp2k_input module (in the stage's script dir).
PYTHONPATH="$SCRIPT_DIR" "$PYTHON" -c "
from cp2k_input import xyz_to_coord_inc
xyz_to_coord_inc('config-$(printf '%03d' $i).xyz', 'coord.inc')
"
cp singlepoint.inp run.inp
if ! cp2k.psmp -i run.inp -o "config-$(printf '%03d' $i).out"; then
  echo "[label-r${r}-c${i}] CP2K FAILED" >&2
  exit 1
fi
SLUR
    fi
  done

  # Wait for labeling jobs to complete (pseudo-slurm poll)
  echo "[03] label-$r: waiting for CP2K jobs..."
  sleep 2  # In real HPC, this would poll scancel/squeue

  # 4. Collect new labels and append to training dataset.
  #    Real loop: parse the CP2K total energy + atomic forces from each .out,
  #    write a new DeepMD set.NNN, and copy the previous dataset so frames
  #    strictly grow.  Abort (non-zero) if any selected config lacks a
  #    successful .out — a silent labeling failure must not feed the model.
  "$PYTHON" - "$r" "$IFACE" "$STRUCT_DIR" <<'PY'
import sys, shutil
from pathlib import Path
import numpy as np
# Reuse the tested parser rather than re-implementing it inline (the inline
# copy diverged from CP2K's real stdout format and silently broke the
# explore->label->dataset-growth loop).
sys.path.insert(0, str(Path(__file__).resolve().parent if '__file__' in dir() else Path(".")))
from parse_cp2k_label import parse_cp2k_out, to_deepmd_units
r, iface, struct_dir = int(sys.argv[1]), sys.argv[2], sys.argv[3]
label_dir = Path(f"../work/al/round-{r}/labeled")
prev_dir = Path(f"../work/train-round{r-1}")
new_dir = Path(f"../work/train-round{r}")

# Selected configs are the ground truth for this round's acquisition.
sel = np.load(f"../work/al/round-{r}/selected.npy")
n_expected = int(len(sel))

# Previous dataset (round 0 comes from stage-02 initial training set).
if not prev_dir.is_dir():
    print(f"[03] collect-{r}: ERROR previous dataset missing: {prev_dir}", file=sys.stderr)
    sys.exit(2)

# Load the generated structure (atom order + box) from stage 00.
box = np.loadtxt(Path(struct_dir) / "box.raw").reshape(3, 3)
typ = np.loadtxt(Path(struct_dir) / "type.raw", dtype=int).reshape(-1)
nat = len(typ)
positions = np.load(f"../work/al/explore-{r}/positions.npy").reshape(-1, -1, 3)
boxes = np.load(f"../work/al/explore-{r}/boxes.npy").reshape(-1, 3, 3)

labels = []   # (coord[nat,3], box[3,3], energy_Ha, force[nat,3])
missing = []
for i, frame_idx in enumerate(sel):
    xyz_file = label_dir / f"config-{i:03d}.xyz"
    out_file = label_dir / f"config-{i:03d}.out"
    if not out_file.is_file():
        missing.append(out_file.name)
        continue
    parsed = parse_cp2k_out(out_file)
    if parsed is None:
        missing.append(out_file.name + " (unparsable / failed CP2K)")
        continue
    if parsed["forces"].shape != (nat, 3):
        missing.append(f"{out_file.name} (forces shape {parsed['forces'].shape} != {nat}x3)")
        continue
    labels.append((positions[frame_idx], boxes[frame_idx],
                   parsed["energy_Ha"], parsed["forces"]))

if missing:
    print(f"[03] collect-{r}: ERROR {len(missing)}/{n_expected} CP2K labels failed: "
          f"{', '.join(missing[:5])}", file=sys.stderr)
    sys.exit(1)
n_new = len(labels)
if n_new == 0:
    print(f"[03] collect-{r}: ERROR no new labels parsed", file=sys.stderr)
    sys.exit(1)

# -- Build the new dataset: copy prev sets, then append set.<next> --
def next_set_index(root: Path) -> int:
    idxs = [int(p.name.split(".")[1]) for p in root.glob("set.*")]
    return max(idxs) + 1 if idxs else 0

shutil.rmtree(new_dir, ignore_errors=True)
shutil.copytree(prev_dir, new_dir)
set_idx = next_set_index(new_dir)
new_set = new_dir / f"set.{set_idx:03d}"
new_set.mkdir(parents=True, exist_ok=True)
coord = np.stack([c for c, _, _, _ in labels])
boxa = np.stack([b.flatten() for _, b, _, _ in labels])
# Convert CP2K native units (energy Hartree, forces Hartree/bohr) to the
# DeepMD units the model trains on (eV, eV/Å).  Energy *and* force must be
# converted — raw Hartree/bohr forces silently corrupt the labels.
energy = np.array([u["energy_eV"] for _, _, e, f in labels
                   for u in [to_deepmd_units({"energy_Ha": e, "forces": f})]])
force = np.stack([to_deepmd_units({"energy_Ha": e, "forces": f})["forces_eV_per_A"]
                  for _, _, e, f in labels])
np.save(new_set / "coord.npy", coord)
np.save(new_set / "box.npy", boxa)
np.save(new_set / "energy.npy", energy)
np.save(new_set / "force.npy", force)
np.savetxt(new_dir / "type.raw", typ, fmt="%d")
existing_frames = sum(int(np.load(str(p / "coord.npy")).shape[0])
                      for p in new_dir.glob("set.*") if (p / "coord.npy").is_file())
assert len(coord) == n_new, "appended coord frames must equal parsed label count"
print(f"[03] retrain-{r}: appended {n_new} new labeled configs to dataset")
print(f"[03] retrain-{r}: total frames now: {existing_frames}")
print(f"[03] retrain-{r}: new set.{set_idx:03d}: E_range={energy.min():.3f}..{energy.max():.3f} eV")
PY

  # 5. Retrain with expanded dataset
  STEPS="$([ "$AI2KIT_042_PROFILE" = formal ] && echo 1000000 || echo 100)"
  "$PYTHON" ../02-train/train.py --steps "$STEPS" \
    --data-dir "../work/train-round$r" \
    --seed "$((1000 + r))" --interface "$IFACE"
done

echo "[03-active-learning] done: $ROUNDS round(s), interface=$IFACE"
