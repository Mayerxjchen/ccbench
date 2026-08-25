import json
import os
import subprocess
from pathlib import Path

# Interpreter pinned by the Harness (VERIFIER_PYTHON): the verifier's own venv,
# outside /root and /app — never an interpreter shipped in the sealed submission.
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")

OUT_PATH = Path("/app/out.json")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.json not found"


def test_json_format():
    data = json.loads(OUT_PATH.read_text())
    assert isinstance(data, list), "expected a JSON list of frequencies"
    assert len(data) == 6, f"expected 6 vibrational modes for formaldehyde (3N-6), got {len(data)}"
    assert all(isinstance(x, (int, float)) for x in data)
    assert data == sorted(data), "frequencies must be sorted ascending"


def test_frequencies_correct():
    result = subprocess.run(
        [VERIFIER_PY, "-c", """
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

tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + '/vib')
vib.run()
freqs = vib.get_frequencies()
vib.clean()
shutil.rmtree(tmpdir, ignore_errors=True)
expected = sorted(sorted(freqs.real, key=abs, reverse=True)[:6])
print(json.dumps([round(float(x), 2) for x in expected]))
"""],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    expected = json.loads(result.stdout.strip().splitlines()[-1])
    got = json.loads(OUT_PATH.read_text())
    assert len(got) == len(expected)
    for g, e in zip(got, expected):
        assert abs(g - e) < 5.0, (got, expected)
