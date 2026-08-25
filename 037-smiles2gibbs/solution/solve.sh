#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
import json
import shutil
import tempfile

from ase import Atoms
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from ase.thermochemistry import IdealGasThermo
from rdkit import Chem
from rdkit.Chem import AllChem
from tblite.ase import TBLite

smi = 'CC=O'
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]

atoms = Atoms(symbols=symbols, positions=conf.GetPositions())
atoms.calc = TBLite(method='GFN2-xTB', verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)
potentialenergy = atoms.get_potential_energy()

n_atoms = len(atoms)
n_modes_real = 3 * n_atoms - 6

tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + '/vib')
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
    geometry='nonlinear',
    symmetrynumber=1,
    spin=0,
)
gibbs = thermo.get_gibbs_energy(temperature=298.15, pressure=101325, verbose=False)

with open('/app/out.json', 'w') as f:
    json.dump({'gibbs_free_energy_eV': round(float(gibbs), 4)}, f)
"
