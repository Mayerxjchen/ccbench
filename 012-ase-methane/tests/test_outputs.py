import os
import subprocess
from pathlib import Path

# Interpreter pinned by the Harness (VERIFIER_PYTHON): the verifier's own venv,
# outside /root and /app — never an interpreter shipped in the sealed submission.
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")

XYZ_PATH = Path("/app/CH4.xyz")


def test_file_exists():
    assert XYZ_PATH.is_file(), "CH4.xyz not found"


def test_xyz_format():
    lines = XYZ_PATH.read_text().strip().splitlines()
    assert len(lines) >= 3, "XYZ file too short"
    n_atoms = int(lines[0].strip())
    assert n_atoms == 5, f"Expected 5 atoms, got {n_atoms}"
    assert len(lines) == n_atoms + 2, f"Expected {n_atoms + 2} lines, got {len(lines)}"


def test_methane_content():
    content = XYZ_PATH.read_text()
    assert "C" in content, "No carbon atom found"
    h_count = content.count("H ")
    assert h_count == 4, f"Expected 4 hydrogen atoms, got {h_count}"


def test_xyz_correct():
    subprocess.run(
        [VERIFIER_PY, "-c",
         "from ase.build import molecule; from ase.io import read; "
         "import numpy as np; "
         "expected = molecule('CH4'); got = read('/app/CH4.xyz'); "
         "assert len(expected) == len(got); "
         "assert all(a.symbol == b.symbol for a, b in zip(expected, got)); "
         "assert np.allclose(expected.get_positions(), got.get_positions(), atol=1e-4)"],
        capture_output=True, text=True, check=True,
    )
