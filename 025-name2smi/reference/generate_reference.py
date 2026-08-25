"""Recompute the ground-truth SMILES for 025-name2smi from the bundled lookup table.

Run inside dftworld-base-chem:
  docker run --rm -v $(pwd):/work -w /work dftworld-base-chem \
    bash -c "source /app/.venv/bin/activate && python3 generate_reference.py"
"""
import json
import sys
from pathlib import Path

from rdkit import Chem
import rdkit

TASK_DIR = Path(__file__).resolve().parent.parent
lookup = json.loads((TASK_DIR / "public" / "name_lookup.json").read_text())

smi = lookup["2-acetoxybenzoic acid"]
canonical = Chem.MolToSmiles(Chem.MolFromSmiles(smi))

reference = {
    "molecule_name": "2-acetoxybenzoic acid",
    "input_smiles": smi,
    "expected_canonical_smiles": canonical,
    "rdkit_version": rdkit.__version__,
}
(Path(__file__).parent / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
print(json.dumps(reference, indent=2))
