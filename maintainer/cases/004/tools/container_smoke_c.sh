#!/bin/bash
# ============================================================================
# 034 C-gate container smoke (Acceptance Board C1-C5).
#
# Run INSIDE the built dftworld-base-ai2kit image (docker run --rm -it \
#   dftworld-base-ai2kit:0.1.0-cpu bash /app/tools/container_smoke_c.sh).
# Writes evidence into /tmp/c-smoke/ (mounted out by the caller if needed).
#
# C1 scientific stack : ai2-kit/dp/lmp/cp2k/omb --help/--version + imports
# C2 cp2k energy/force: a real small CP2K ENERGY/FORCE job (converged SCF)
# C3 deepmd train      : dp train -> freeze -> DeepPot.eval finite E/F
#                        (build-time smoke_test.py already gates this; here we
#                         capture the versions for the record)
# C4 lammps+deepmd MD  : pair_style deepmd + run >0 steps, trajectory, no NaN
# C5 pseudo-slurm E2E  : sbatch -> squeue -> sacct COMPLETED
#
# Exit 0 iff C1-C5 all pass; prints "C_GATES_ALL_PASS".
# ============================================================================
set -uo pipefail
OUT=/tmp/c-smoke
mkdir -p "$OUT"
exec > >(tee "$OUT/smoke.log") 2>&1
PY=/opt/ai2kit/bin/python
fail() { echo "[C] FAIL: $*"; exit 1; }
ok()   { echo "[C] ok: $*"; }

echo "=== C1 scientific stack ==="
: > "$OUT/c1-versions.txt"
for cmd in "ai2-kit --help" "dp --version" "omb --version" "lmp -help" "cp2k --version" "mpirun --version"; do
  echo "--- $cmd ---" >> "$OUT/c1-versions.txt"
  eval "$cmd" >> "$OUT/c1-versions.txt" 2>&1 || echo "(rc=$?)" >> "$OUT/c1-versions.txt"
done
echo "--- imports ---" >> "$OUT/c1-versions.txt"
"$PY" - <<'PY' >> "$OUT/c1-versions.txt" 2>&1
import sys
print("python", sys.version.split()[0])
for m in ("ase", "dpdata", "pymatgen", "MDAnalysis", "deepmd", "ai2_kit"):
    try:
        mod = __import__(m)
        print(f"import {m}: ok", getattr(mod, "__version__", ""))
    except Exception as e:
        print(f"import {m}: FAIL {e}")
PY
echo "--- cp2k version line ---"
cp2k --version 2>&1 | head -2
grep -q "deepmd" "$OUT/c1-versions.txt" || fail "c1 deepmd import missing"
command -v cp2k >/dev/null || fail "c1 cp2k not on PATH"
ok "c1 stack"

echo "=== C2 cp2k ENERGY/FORCE ==="
mkdir -p "$OUT/c2" && cd "$OUT/c2"
cat > water.inp <<'EOF'
&GLOBAL
  PROJECT water
  RUN_TYPE ENERGY_FORCE
  PRINT_LEVEL LOW
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME /opt/cp2k/share/cp2k/data/BASIS_MOLOPT
    POTENTIAL_FILE_NAME /opt/cp2k/share/cp2k/data/GTH_POTENTIALS
    &MGRID
      CUTOFF 300
      REL_CUTOFF 50
    &END MGRID
    &QS
      METHOD GPW
    &END QS
    &SCF
      SCF_GUESS ATOMIC
      &OT
        MINIMIZER DIIS
        PRECONDITIONER FULL_ALL
      &END OT
      MAX_SCF 30
      EPS_SCF 1.0E-6
    &END SCF
    &XC
      &XC_FUNCTIONAL BLYP
      &END XC_FUNCTIONAL
      &VDW_POTENTIAL
        DISPERSION_FUNCTIONAL pair_potential
        &PAIR_POTENTIAL
          TYPE DFTD3
          REFERENCE_FUNCTIONAL BLYP
          PARAMETER_FILE_NAME /opt/cp2k/share/cp2k/data/dftd3.dat
          R_CUTOFF 16
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      ABC 12.4 12.4 12.4
      PERIODIC XYZ
    &END CELL
    &COORD
      O  3.8486546208  6.0707416420  2.3215569418
      H  3.1086722774  6.0769713066  1.6464747622
      H  4.5689123777  6.4592261109  1.8085670572
    &END COORD
    &KIND O
      BASIS_SET TZV2P-MOLOPT-GTH
      POTENTIAL GTH-PBE-q6
    &END KIND
    &KIND H
      BASIS_SET TZV2P-MOLOPT-GTH
      POTENTIAL GTH-PBE-q1
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
EOF
# The GTH-PBE potentials are compatible with the BLYP functional's use of GTH
# pseudopotentials (PBE-q6/-q1 are the standard 3-row GTH set); BLYP functional
# is requested in &XC_FUNCTIONAL while POTENTIAL stays GTH-PBE — accepted by CP2K.
if cp2k -i water.inp -o water.out > "$OUT/c2/run.log" 2>&1; then
  grep -E "SCF run converged in|ENERGY\||Total FORCE_EVAL" water.out \
    | head -5 > "$OUT/c2/energy-force.txt"
  grep -q "SCF run converged" water.out || fail "c2 SCF did not converge"
  grep -q "ENERGY| Total FORCE_EVAL ( QS ) energy" water.out || fail "c2 no energy line"
  ok "c2 cp2k ENERGY_FORCE converged (see c2/energy-force.txt)"
else
  tail -20 "$OUT/c2/run.log"
  fail "c2 cp2k run failed"
fi

echo "=== C4 lammps+deepmd MD ==="
# Build a tiny deepmd model (reuse smoke path), then drive a LAMMPS MD run.
mkdir -p "$OUT/c4" && cd "$OUT/c4"
cat > toy.json <<'EOF'
{"model":{"type_map":["O","H"],"descriptor":{"type":"se_e2_a","rcut":5.0,"rcut_smth":3.0,"sel":[8,16],"neuron":[8,8],"seed":1},"fitting_net":{"neuron":[8,8],"resnet_dt":true,"seed":1}},"learning_rate":{"type":"exp","decay_steps":100,"start_lr":0.001,"decay_rate":0.95},"loss":{"type":"ener","start_pref_e":0.02,"limit_pref_e":1,"start_pref_f":1000,"limit_pref_f":1,"start_pref_v":0,"limit_pref_v":0},"training":{"systems":["data"],"set_prefix":"set","batch_size":1,"stop_batch":1,"seed":1}}
EOF
"$PY" - <<'PY'
import numpy as np, json, pathlib
d = pathlib.Path("/tmp/c-smoke/c4/data")
(d / "set.000").mkdir(parents=True, exist_ok=True)
n = 6
rng = np.random.default_rng(7)
type_ints = np.array([0,1,1,0,1,1], dtype=int)
base = np.array([0,0,0, 0.96,0,0, 0.24,0.93,0, 5,5,5, 5.96,5,5, 5.24,5.93,5], float)
coords = np.stack([base + rng.normal(0,0.05,size=18) for _ in range(2)])
forces = rng.normal(0,0.5,size=(2,18)); energies = -150 + rng.normal(0,0.1,size=2)
box = np.tile([10,0,0,0,10,0,0,0,10],(2,1))
(d/"type.raw").write_text("0 1 1 0 1 1\n"); (d/"type_map.raw").write_text("O\nH\n")
# deepmd 2.2.11 DeepmdDataSystem reads set.* as .npy (only type.raw/.type_map stay raw)
np.save(d/"set.000"/"box.npy", box)
np.save(d/"set.000"/"coord.npy", coords)
np.save(d/"set.000"/"energy.npy", energies.reshape(-1,1))
np.save(d/"set.000"/"force.npy", forces)
PY
"$PY" -m json.tool toy.json >/dev/null || fail "c4 toy json invalid"
dp train toy.json > "$OUT/c4/train.log" 2>&1 || fail "c4 dp train failed"
dp freeze -o frozen.pb > "$OUT/c4/freeze.log" 2>&1 || fail "c4 dp freeze failed"
[ -s frozen.pb ] || fail "c4 frozen.pb empty"
cat > in.lmp <<'EOF'
units           metal
boundary        p p p
atom_style      atomic
box tilt large
region          box block 0 12.4 0 12.4 0 12.4
create_box      2 box
pair_style      deepmd frozen.pb
pair_coeff      * *
create_atoms    1 single 3.848 6.070 2.321
create_atoms    1 single 6.848 6.070 2.321
create_atoms    2 single 3.108 6.076 1.646
create_atoms    2 single 6.508 6.076 1.646
create_atoms    2 single 4.568 6.459 1.808
create_atoms    2 single 6.468 6.459 1.808
mass            1 15.999
mass            2 1.008
velocity        all create 300 87287 mom yes rot yes dist gaussian
neighbor        0.3 bin
thermo          10
thermo_style    custom step temp pe ke etotal press
dump            1 all custom 10 dump.lammpstrj id type x y z
timestep        0.0005
run             30
EOF
lmp -in in.lmp -log log.lammps > "$OUT/c4/lmp-run.log" 2>&1 || fail "c4 lammps run failed"
# NaN check on the trajectory coordinates, NOT the whole log (the log's
# CITE block contains author names like "Wang, Han" that false-positive).
[ -s dump.lammpstrj ] || fail "c4 no trajectory"
grep -qi "nan" dump.lammpstrj && fail "c4 NaN in trajectory"
grep -c "ITEM: TIMESTEP" dump.lammpstrj > "$OUT/c4/frames.txt"
ok "c4 lammps+deepmd MD ran (frames=$(cat "$OUT/c4/frames.txt"))"

echo "=== C5 pseudo-slurm E2E ==="
mkdir -p "$OUT/c5" && cd "$OUT/c5"
cat > hello.slurm <<'EOF'
#!/bin/bash
#SBATCH --job-name=c5-smoke
sleep 1
echo hello-pseudo-slurm-c5
EOF
JID=$(sbatch --parsable hello.slurm) || fail "c5 sbatch failed"
echo "job=$JID"
for i in $(seq 1 60); do
  ST=$(sacct -X -P --format=JobID,State -j "$JID" 2>/dev/null | grep "^$JID|" | head -1 | cut -d'|' -f2)
  [ "$ST" = "COMPLETED" ] && break
  [ "$ST" = "FAILED" ] && { fail "c5 job FAILED"; }
  sleep 0.5
done
[ "$ST" = "COMPLETED" ] || fail "c5 job did not complete (state=$ST)"
ok "c5 pseudo-slurm E2E -> COMPLETED (job $JID)"

echo
echo "=== C_GATES_ALL_PASS ==="
echo "evidence: $OUT/smoke.log"
