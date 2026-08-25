#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
import json
import shutil
import tempfile

from ase import Atoms
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from rdkit import Chem
from rdkit.Chem import AllChem
from tblite.ase import TBLite

smi = 'C=O'
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]

atoms = Atoms(symbols=symbols, positions=conf.GetPositions())
atoms.calc = TBLite(method='GFN2-xTB', verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)

n_atoms = len(atoms)
n_modes_real = 3 * n_atoms - 6

tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + '/vib')
vib.run()
freqs = vib.get_frequencies()
vib.clean()
shutil.rmtree(tmpdir, ignore_errors=True)

real_freqs = sorted(sorted(freqs.real, key=abs, reverse=True)[:n_modes_real])

with open('/app/out.json', 'w') as f:
    json.dump([round(float(x), 2) for x in real_freqs], f)
"
