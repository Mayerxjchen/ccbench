"""Recompute the ground-truth vibrational frequencies for 036-smiles2vib.

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
from ase.vibrations import Vibrations
from rdkit import Chem
from rdkit.Chem import AllChem
from tblite.ase import TBLite

smi = "C=O"
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]

atoms = Atoms(symbols=symbols, positions=conf.GetPositions())
atoms.calc = TBLite(method="GFN2-xTB", verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)

n_atoms = len(atoms)
n_modes_real = 3 * n_atoms - 6

tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + "/vib")
vib.run()
freqs = vib.get_frequencies()
vib.clean()
shutil.rmtree(tmpdir, ignore_errors=True)

real_freqs = sorted(sorted(freqs.real, key=abs, reverse=True)[:n_modes_real])

reference = {
    "molecule_name": "formaldehyde",
    "input_smiles": smi,
    "n_atoms": n_atoms,
    "symbols": atoms.get_chemical_symbols(),
    "embed_seed": 42,
    "fmax": 0.02,
    "vibrational_frequencies_cm1": [round(float(x), 2) for x in real_freqs],
    "rdkit_version": rdkit.__version__,
    "ase_version": ase.__version__,
    "tblite_version": importlib.metadata.version("tblite"),
}
(Path(__file__).parent / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
print(json.dumps(reference, indent=2))
