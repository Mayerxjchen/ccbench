#!/bin/bash
# Oracle: SO2 geometry optimization with MACE-MP-0 + save optimized XYZ + result.json.
# Runs inside the task container with /app = agent workspace (public inputs).
# Faithfully mirrors the source run_ase 'opt' driver (ase_core.py).
set -e
source /app/.venv/bin/activate
python3 << 'PYEOF'
import json
from ase.io import read, write
from ase.optimize import BFGS
from mace.calculators import mace_mp

atoms = read("/app/SO2.xyz")
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")

dyn = BFGS(atoms)
converged = dyn.run(fmax=0.01, steps=1000)
energy = float(atoms.get_potential_energy())

# artifact gate: save the optimized (final converged) geometry as a standard XYZ file
write("/app/optimized.xyz", atoms, format="xyz")

result = {
    "molecule": "SO2",
    "method": "mace_mp / medium-mpa-0",
    "converged": bool(converged),
    "optimized_energy_ev": energy,
    "structure_file": "optimized.xyz",
}
with open("/app/result.json", "w") as f:
    json.dump(result, f, indent=2)

print("E = %.12f eV  converged = %s" % (energy, converged))
print("wrote /app/result.json and /app/optimized.xyz")
PYEOF
