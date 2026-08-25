#!/bin/bash
# Oracle: CO2 thermochemistry (H/S/G) at 800 K via GFN2-xTB.
# Runs inside the task container with /app = agent workspace (public inputs).
# Faithfully mirrors the source run_ase 'thermo' driver.
set -e
source /app/.venv/bin/activate
python3 << 'PYEOF'
import json, os, tempfile
import numpy as np
from ase.io import read, write
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from ase.thermochemistry import IdealGasThermo
from tblite.ase import TBLite

T = 800.0
P = 101325.0


def is_linear(atoms, tol=1e-3):
    c = np.array(atoms.positions)
    c = c - c.mean(axis=0)
    _, s, _ = np.linalg.svd(c)
    return False if s[0] == 0 else (s[1] / s[0]) < tol


def symmetry_number(atoms):
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.symmetry.analyzer import PointGroupAnalyzer
    return PointGroupAnalyzer(
        AseAtomsAdaptor().get_molecule(atoms)
    ).get_rotational_symmetry_number()


def thermo(xyz, out_xyz):
    atoms = read(xyz)
    atoms.calc = TBLite(method="GFN2-xTB")
    dyn = BFGS(atoms)
    dyn.run(fmax=0.01, steps=1000)
    # deliver the optimized geometry as a raw artifact (standard XYZ)
    write(out_xyz, atoms, format="xyz")
    E = float(atoms.get_potential_energy())
    if len(atoms) == 1:
        return {"enthalpy_ev": E, "gibbs_free_energy_ev": E, "entropy_ev_per_k": 0.0}
    with tempfile.TemporaryDirectory(prefix="cg_vib_") as td:
        vib = Vibrations(atoms, name=os.path.join(td, "vib"))
        vib.run()
        energies = vib.get_energies()
    linear = is_linear(atoms)
    th = IdealGasThermo(
        vib_energies=energies,
        potentialenergy=E,
        atoms=atoms,
        geometry="linear" if linear else "nonlinear",
        symmetrynumber=symmetry_number(atoms),
        spin=0.0,
    )
    return {
        "enthalpy_ev": float(th.get_enthalpy(temperature=T)),
        "entropy_ev_per_k": float(th.get_entropy(temperature=T, pressure=P)),
        "gibbs_free_energy_ev": float(th.get_gibbs_energy(temperature=T, pressure=P)),
    }


r = thermo("/app/CO2.xyz", "/app/optimized.xyz")
result = {
    "molecule": "CO2",
    "temperature": 800,
    "pressure_pa": 101325,
    "method": "GFN2-xTB",
    "enthalpy_ev": r["enthalpy_ev"],
    "entropy_ev_per_k": r["entropy_ev_per_k"],
    "gibbs_free_energy_ev": r["gibbs_free_energy_ev"],
}
with open("/app/result.json", "w") as f:
    json.dump(result, f, indent=2)
print("G(800K) = %.6f eV  H = %.6f eV  S = %.8f eV/K" % (
    r["gibbs_free_energy_ev"], r["enthalpy_ev"], r["entropy_ev_per_k"]))
PYEOF
