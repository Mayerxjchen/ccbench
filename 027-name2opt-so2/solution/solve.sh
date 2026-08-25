#!/bin/bash
# Oracle: SO2 geometry optimization via MACE-MP-0 (medium-mpa-0).
# Runs inside the task container with /app = agent workspace (public inputs).
# Faithfully mirrors the source run_ase 'opt' driver:
#   calculator : mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
#   optimizer  : BFGS, fmax=0.01 eV/A, max 1000 steps
set -e
source /app/.venv/bin/activate
python3 << 'PYEOF'
import json
from ase.io import read
from ase.optimize import BFGS
from mace.calculators import mace_mp

atoms = read("/app/SO2.xyz")
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
dyn = BFGS(atoms)
converged = dyn.run(fmax=0.01, steps=1000)
energy = float(atoms.get_potential_energy())

# Save the optimized structure as a clean standard XYZ.
with open("/app/optimized.xyz", "w") as fh:
    fh.write(f"{len(atoms)}\nSO2\n")
    for atom in atoms:
        x, y, z = atom.position
        fh.write(f"{atom.symbol} {x: .10f} {y: .10f} {z: .10f}\n")

result = {
    "molecule": "SO2",
    "method": "mace_mp / medium-mpa-0",
    "converged": bool(converged),
    "optimized_energy_ev": energy,
}
with open("/app/result.json", "w") as f:
    json.dump(result, f, indent=2)
print("optimized energy = %.8f eV (converged=%s, nsteps=%d)"
      % (energy, converged, dyn.nsteps))
PYEOF
