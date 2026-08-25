#!/bin/bash
source /app/.venv/bin/activate
python3 << 'PYEOF'
from rdkit import Chem
from rdkit.Chem import AllChem
import numpy as np

mol = Chem.MolFromSmiles('CN1C=NC2=C1C(=O)N(C(=O)N2C)C')
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)

conf = mol.GetConformer()
positions = conf.GetPositions()

vdw = {'H': 1.20, 'C': 1.70, 'N': 1.55, 'O': 1.52}
spacing = 0.25
min_c = positions.min(axis=0) - 4
max_c = positions.max(axis=0) + 4
x = np.arange(min_c[0], max_c[0], spacing)
y = np.arange(min_c[1], max_c[1], spacing)
z = np.arange(min_c[2], max_c[2], spacing)
xx, yy, zz = np.meshgrid(x, y, z, indexing='ij')
grid = np.stack([xx.ravel(), yy.ravel(), zz.ravel()], axis=1)

inside = np.zeros(len(grid), dtype=bool)
for i, atom in enumerate(mol.GetAtoms()):
    r = vdw[atom.GetSymbol()]
    dist2 = np.sum((grid - positions[i])**2, axis=1)
    inside |= (dist2 <= r*r)

vol = inside.sum() * spacing**3
with open('/app/out.txt', 'w') as f:
    f.write(f"{vol:.2f}")
PYEOF
