#!/bin/bash
# Oracle: H2O vibrational frequencies via MACE-MP-0 medium.
# Runs inside the task container with /app = agent workspace (public inputs).
# Faithfully mirrors the source run_ase_core 'vib' driver:
#   mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
#   -> BFGS opt (fmax=0.01, steps=1000) -> Vibrations(...).run()
#   -> keep 3N-6 = 3 real modes (non-linear H2O drops the first 6).
set -e
source /app/.venv/bin/activate
python3 << 'PYEOF'
import json, os, tempfile
import numpy as np
from ase import units
from ase.io import read, write
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from mace.calculators import mace_mp


def is_linear(positions, tol=1e-3):
    c = np.array(positions) - np.mean(np.array(positions), axis=0)
    _, s, _ = np.linalg.svd(c)
    return False if s[0] == 0 else (s[1] / s[0]) < tol


def vib_mode_indices(positions, total_modes):
    n = len(positions)
    assert total_modes == 3 * n
    if n == 1:
        return []
    num_nonvib = 5 if is_linear(positions) else 6
    return list(range(num_nonvib, total_modes))


atoms = read("/app/H2O.xyz")
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")

dyn = BFGS(atoms)
dyn.run(fmax=0.01, steps=1000)

# artifact gate: save the optimized (final converged) geometry as standard XYZ
write("/app/optimized.xyz", atoms, format="xyz")

with tempfile.TemporaryDirectory(prefix="cg_vib_") as td:
    vib = Vibrations(atoms, name=os.path.join(td, "vib"))
    vib.run()
    all_energies = vib.get_energies()

positions = np.array(atoms.positions)
mode_indices = vib_mode_indices(positions, len(all_energies))

vibrational_modes = []
for mi in mode_indices:
    e = all_energies[mi]
    is_imag = abs(e.imag) > 1e-8
    e_val = e.imag if is_imag else e.real
    vibrational_modes.append({
        "energy_mev": float(1e3 * e_val),
        "frequency_cm1": float(e_val / units.invcm),
    })

vibrational_modes.sort(key=lambda m: m["frequency_cm1"])

result = {
    "molecule": "H2O",
    "method": "mace_mp / medium-mpa-0",
    "vibrational_modes": vibrational_modes,
}
with open("/app/result.json", "w") as f:
    json.dump(result, f, indent=2)

print("H2O vibrational frequencies (MACE-MP-0 medium):")
for m in vibrational_modes:
    print("  %10.4f meV  %10.4f cm-1" % (m["energy_mev"], m["frequency_cm1"]))
PYEOF
