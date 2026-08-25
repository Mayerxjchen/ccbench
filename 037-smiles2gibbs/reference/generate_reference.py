"""Recompute the ground-truth Gibbs free energy for 037-smiles2gibbs.

Run inside dftworld-base-xtb:
  docker run --rm -v $(pwd):/work -w /work dftworld-base-xtb \
    bash -c "source /app/.venv/bin/activate && python3 generate_reference.py"
"""
import importlib.metadata
import json
import shutil
import tempfile
from pathlib import Path

import ase
import rdkit
from ase import Atoms
from ase.optimize import BFGS
from ase.thermochemistry import IdealGasThermo
from ase.vibrations import Vibrations
from rdkit import Chem
from rdkit.Chem import AllChem
from tblite.ase import TBLite

smi = "CC=O"
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]

atoms = Atoms(symbols=symbols, positions=conf.GetPositions())
atoms.calc = TBLite(method="GFN2-xTB", verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)
potentialenergy = atoms.get_potential_energy()

n_atoms = len(atoms)
n_modes_real = 3 * n_atoms - 6

tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + "/vib")
vib.run()
energies = vib.get_energies()
vib.clean()
shutil.rmtree(tmpdir, ignore_errors=True)

vib_energies = sorted(energies, key=lambda e: abs(e), reverse=True)[:n_modes_real]
vib_energies = [complex(e).real for e in vib_energies]

thermo = IdealGasThermo(
    vib_energies=vib_energies,
    potentialenergy=potentialenergy,
    atoms=atoms,
    geometry="nonlinear",
    symmetrynumber=1,
    spin=0,
)
gibbs = thermo.get_gibbs_energy(temperature=298.15, pressure=101325, verbose=False)

reference = {
    "molecule_name": "acetaldehyde",
    "input_smiles": smi,
    "n_atoms": n_atoms,
    "symbols": atoms.get_chemical_symbols(),
    "embed_seed": 42,
    "fmax": 0.02,
    "symmetrynumber": 1,
    "temperature_K": 298.15,
    "pressure_Pa": 101325,
    "potentialenergy_eV": float(potentialenergy),
    "gibbs_free_energy_eV": round(float(gibbs), 4),
    "rdkit_version": rdkit.__version__,
    "ase_version": ase.__version__,
    "tblite_version": importlib.metadata.version("tblite"),
}
(Path(__file__).parent / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
print(json.dumps(reference, indent=2))
