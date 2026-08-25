import os
import subprocess
from pathlib import Path

# Interpreter pinned by the Harness (VERIFIER_PYTHON): the verifier's own venv,
# outside /root and /app — never an interpreter shipped in the sealed submission.
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")

OUT_PATH = Path("/app/out.xyz")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.xyz not found"


def test_xyz_format():
    lines = OUT_PATH.read_text().strip().splitlines()
    assert len(lines) >= 3, "XYZ file too short"
    n_atoms = int(lines[0].strip())
    assert n_atoms == 33, f"Expected 33 atoms (C13H18O2), got {n_atoms}"
    assert len(lines) == n_atoms + 2, f"Expected {n_atoms + 2} lines, got {len(lines)}"


def test_formula():
    content = OUT_PATH.read_text()
    rows = content.splitlines()[2:]
    c_count = sum(1 for line in rows if line.strip().startswith("C "))
    o_count = sum(1 for line in rows if line.strip().startswith("O "))
    h_count = sum(1 for line in rows if line.strip().startswith("H "))
    assert (c_count, o_count, h_count) == (13, 2, 18), f"Expected C13H18O2, got C{c_count}O{o_count}H{h_count}"


def test_coordinates_correct():
    result = subprocess.run(
        [VERIFIER_PY, "-c", """
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

smi = 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O'
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
expected_symbols = [a.GetSymbol() for a in mol.GetAtoms()]
expected_pos = mol.GetConformer().GetPositions()

lines = open('/app/out.xyz').read().strip().splitlines()
n = int(lines[0])
got_symbols = []
got_pos = []
for line in lines[2:2+n]:
    parts = line.split()
    got_symbols.append(parts[0])
    got_pos.append([float(parts[1]), float(parts[2]), float(parts[3])])
got_pos = np.array(got_pos)

assert got_symbols == expected_symbols, (got_symbols, expected_symbols)
assert np.allclose(got_pos, expected_pos, atol=1e-3), (got_pos, expected_pos)
print('ok')
"""],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
