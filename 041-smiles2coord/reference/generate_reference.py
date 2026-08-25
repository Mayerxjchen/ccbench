"""Recompute the ground-truth embedded conformer for 041-smiles2coord.

Run inside dftworld-base-chem:
  docker run --rm -v $(pwd):/work -w /work dftworld-base-chem \
    bash -c "source /app/.venv/bin/activate && python3 generate_reference.py"
"""
import json
from pathlib import Path

import rdkit
from rdkit import Chem
from rdkit.Chem import AllChem

smi = "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O"
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
positions = mol.GetConformer().GetPositions()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]

reference = {
    "molecule_name": "ibuprofen",
    "input_smiles": smi,
    "n_atoms": len(symbols),
    "symbols": symbols,
    "embed_seed": 42,
    "positions_angstrom": positions.tolist(),
    "rdkit_version": rdkit.__version__,
}
(Path(__file__).parent / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
print(json.dumps({k: v for k, v in reference.items() if k != "positions_angstrom"}, indent=2))
