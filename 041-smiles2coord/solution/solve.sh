#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
from rdkit import Chem
from rdkit.Chem import AllChem

smi = 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O'
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]
positions = conf.GetPositions()

with open('/app/out.xyz', 'w') as f:
    f.write(f'{len(symbols)}\n')
    f.write('ibuprofen embedded conformer (RDKit)\n')
    for s, (x, y, z) in zip(symbols, positions):
        f.write(f'{s} {x:.6f} {y:.6f} {z:.6f}\n')
"
