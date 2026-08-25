#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
from rdkit import Chem
from rdkit.Chem import AllChem
from ase import Atoms
from ase.optimize import BFGS
from tblite.ase import TBLite

smi = 'CC(=O)C'
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]
positions = conf.GetPositions()

atoms = Atoms(symbols=symbols, positions=positions)
atoms.calc = TBLite(method='GFN2-xTB', verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)

with open('/app/acetone.xyz', 'w') as f:
    f.write(f'{len(atoms)}\n')
    f.write('acetone optimized (GFN2-xTB)\n')
    for s, (x, y, z) in zip(atoms.get_chemical_symbols(), atoms.get_positions()):
        f.write(f'{s} {x:.6f} {y:.6f} {z:.6f}\n')
"
