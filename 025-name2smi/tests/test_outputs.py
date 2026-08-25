import json
import os
import subprocess
from pathlib import Path

# Interpreter pinned by the Harness (VERIFIER_PYTHON): the verifier's own venv,
# outside /root and /app — never an interpreter shipped in the sealed submission.
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")

OUT_PATH = Path("/app/out.txt")
LOOKUP_PATH = Path("/app/name_lookup.json")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_smiles_valid():
    subprocess.run(
        [VERIFIER_PY, "-c",
         "from rdkit import Chem; import sys; "
         "smi = open('/app/out.txt').read().strip(); "
         "mol = Chem.MolFromSmiles(smi); "
         "assert mol is not None, f'invalid SMILES: {smi!r}'"],
        capture_output=True, text=True, check=True,
    )


def test_smiles_correct():
    lookup = json.loads(LOOKUP_PATH.read_text())
    result = subprocess.run(
        [VERIFIER_PY, "-c",
         f"from rdkit import Chem; "
         f"expected = Chem.MolToSmiles(Chem.MolFromSmiles({lookup['2-acetoxybenzoic acid']!r})); "
         f"got_raw = open('/app/out.txt').read().strip(); "
         f"got = Chem.MolToSmiles(Chem.MolFromSmiles(got_raw)); "
         f"assert expected == got, (expected, got); "
         f"print('ok')"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
