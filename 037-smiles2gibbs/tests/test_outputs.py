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
    assert "gibbs_free_energy_eV" in data
    assert isinstance(data["gibbs_free_energy_eV"], (int, float))


def test_gibbs_energy_correct():
    result = subprocess.run(
        [VERIFIER_PY, "-c", """
import json
import shutil
import tempfile

from ase import Atoms
from ase.optimize import BFGS
from ase.thermochemistry import IdealGasThermo
from ase.vibrations import Vibrations
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

n_modes_real = 3 * len(atoms) - 6
tmpdir = tempfile.mkdtemp()
vib = Vibrations(atoms, name=tmpdir + '/vib')
vib.run()
energies = vib.get_energies()
vib.clean()
shutil.rmtree(tmpdir, ignore_errors=True)
vib_energies = [complex(e).real for e in sorted(energies, key=lambda e: abs(e), reverse=True)[:n_modes_real]]

thermo = IdealGasThermo(
    vib_energies=vib_energies,
    potentialenergy=potentialenergy,
    atoms=atoms,
    geometry='nonlinear',
    symmetrynumber=1,
    spin=0,
)
gibbs = thermo.get_gibbs_energy(temperature=298.15, pressure=101325, verbose=False)
print(json.dumps({'gibbs_free_energy_eV': round(float(gibbs), 4)}))
"""],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    expected = json.loads(result.stdout.strip().splitlines()[-1])["gibbs_free_energy_eV"]
    got = json.loads(OUT_PATH.read_text())["gibbs_free_energy_eV"]
    assert abs(got - expected) < 0.01, (got, expected)
