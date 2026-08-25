#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
import json
from rdkit import Chem
from rdkit.Chem import AllChem

d = json.load(open('/app/name_lookup.json'))
smi = d['ethanol']
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)

conf = mol.GetConformer()
positions = conf.GetPositions()
symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]

with open('/app/out.xyz', 'w') as f:
    f.write(f'{len(symbols)}\n')
    f.write('ethanol\n')
    for s, (x, y, z) in zip(symbols, positions):
        f.write(f'{s} {x:.6f} {y:.6f} {z:.6f}\n')
"
