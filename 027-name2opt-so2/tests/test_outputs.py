"""027-name2opt-so2 — hidden evaluator.

Layers (acceptance_standard.md G6):
  L1 Artifact     /app/result.json exists, parseable, *exact* schema
                  {molecule, method, converged, optimized_energy_ev};
                  /app/optimized.xyz exists and parses via ase.io.read
  L2 Execution    genuine MACE evaluation terminates and yields a finite energy
                  (verified by L4, which re-runs MACE on optimized.xyz)
  L3 Identity     molecule == "SO2", method exactly identifies mace_mp / medium-mpa-0,
                  converged == true
  L4 Consistency  optimized_energy_ev == MACE single-point energy re-evaluated
                  on the optimized.xyz structure (anti-forgery)
  L5 Scientific   energy in physical MACE band; composition preserved (1S+2O);
                  geometry actually moved (rigid-body-free displacement);
                  max|force| on optimized.xyz < 0.01 eV/A (the BFGS fmax
                  criterion — proves the geometry is a genuine converged
                  stationary point, not a translated single-point echo)
  L6 Reference    optimized_energy_ev agrees with the regenerated reference
                  within tolerance

Notes on the test runtime:
  * The verifier runs `pytest` with the venv interpreter pinned by the
    Harness (VERIFIER_PYTHON=/opt/dftworld/venv/bin/python, outside
    /root and /app).  numpy/ase are pulled from the verifier venv's
    site-packages via sys.path injection (same pattern as 028); the MACE
    re-evaluation (L4/L5) shells out to VERIFIER_PYTHON so the model load
    stays isolated.

Reference values are frozen from reference/regenerated_reference.json
(generated on the pinned dftworld-base-mace image, BFGS fmax=0.01 steps=1000).
"""
import json
import math
import os
import subprocess
import sys

from collections import Counter
from pathlib import Path

# numpy/ase come from the verifier venv (the Harness-pinned interpreter,
# never one shipped in the sealed submission).
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")
_VENV_SP = Path(VERIFIER_PY).resolve().parent.parent / "lib/python3.12/site-packages"
if _VENV_SP.is_dir() and str(_VENV_SP) not in sys.path:
    sys.path.insert(0, str(_VENV_SP))

import numpy as np

RESULT_PATH = Path("/app/result.json")
OPTIMIZED_PATH = Path("/app/optimized.xyz")
INITIAL_PATH = Path("/app/SO2.xyz")
CANONICAL_METHOD = "mace_mp / medium-mpa-0"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())

# ---- frozen regenerated reference (025, mace_mp medium-mpa-0, pinned image) --
REF_ENERGY = -16.815808388438125  # eV (regenerated on dftworld-base-mace)
# original reference (source ground_truth id=5) = -16.815808019358535 eV,
# |orig - regenerated| = 3.7e-7 eV.  MACE-MP-0 is deterministic (G8:
# bit-identical reruns) and BFGS/LBFGS both land on this value exactly, so a
# 1e-3 eV tolerance is comfortable and still rejects fabricated energies.
REF_TOL_EV = 1e-3

# ---- L4 anti-forgery tolerance (reported vs. single-point on optimized.xyz) --
L4_TOL_EV = 1e-3

# ---- L5 scientific band (MACE-MP-0 total energy, SO2 ~ -16.8 eV) ------------
L5_MIN_EV = -25.0
L5_MAX_EV = -10.0

# ---- L5 force-convergence threshold = the BFGS fmax used by the oracle -------
FMAX_CONV_EV_ANG = 0.01  # eV/A  (max |force| below this => converged)


def load():
    assert RESULT_PATH.is_file(), f"{RESULT_PATH} not found (L1)"
    data = json.loads(RESULT_PATH.read_text())
    assert isinstance(data, dict), "result.json must be a JSON object (L1)"
    return data


# ---- venv helpers: structure parsing & MACE run via the task venv -----------
# The verifier's pytest runs with the venv interpreter pinned by the Harness
# (VERIFIER_PYTHON=/opt/dftworld/venv/bin/python, outside /root and /app);
# all MACE work shells out to that same interpreter so the model load stays
# isolated.  numpy/ase for geometry work (Kabsch etc.) are injected from the
# venv site-packages.

_READ_XYZ_SCRIPT = r"""
import sys, json
from ase.io import read
atoms = read(sys.argv[1])
print("ATOMS_JSON=" + json.dumps({
    "symbols": atoms.get_chemical_symbols(),
    "positions": atoms.positions.tolist(),
    "n_atoms": len(atoms),
}))
"""

_MACE_SP_SCRIPT = r"""
import warnings, sys
warnings.filterwarnings("ignore")
import numpy as np
from ase.io import read
from mace.calculators import mace_mp
atoms = read(sys.argv[1])
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
E = float(atoms.get_potential_energy())
forces = np.asarray(atoms.get_forces())
fmax = float(np.linalg.norm(forces, axis=1).max())
print("SP_ENERGY=" + repr(E))
print("SP_FMAX=" + repr(fmax))
"""


def _venv(script, path, marker):
    r = subprocess.run(
        [VERIFIER_PY, "-c", script, str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"venv subprocess failed: {r.stderr[-600:]}"
    for line in r.stdout.splitlines():
        if line.startswith(marker + "="):
            return line.split("=", 1)[1]
    raise AssertionError(f"no {marker} in subprocess output: {r.stdout[-400:]}")


def _venv_multi(script, path):
    r = subprocess.run(
        [VERIFIER_PY, "-c", script, str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"venv subprocess failed: {r.stderr[-600:]}"
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def read_xyz(path):
    """Parse an XYZ file with the venv's ase (returns symbols, positions, n)."""
    raw = _venv(_READ_XYZ_SCRIPT, path, "ATOMS_JSON")
    return json.loads(raw)


_MACE_CACHE = {}


def mace_eval(xyz_path):
    """(energy_eV, max_abs_force) of an xyz file via MACE-MP-0, in the venv.

    One subprocess call returns both the single-point energy (L4) and the max
    |force| (L5) so MACE loads only once per structure.  Cached by path because
    several tests use the same /app/optimized.xyz.
    """
    p = str(xyz_path)
    if p not in _MACE_CACHE:
        vals = _venv_multi(_MACE_SP_SCRIPT, xyz_path)
        assert "SP_ENERGY" in vals and "SP_FMAX" in vals, (
            f"missing SP markers in output: {vals}"
        )
        _MACE_CACHE[p] = (float(vals["SP_ENERGY"]), float(vals["SP_FMAX"]))
    return _MACE_CACHE[p]


def mace_single_point(xyz_path):
    """Evaluate the MACE-MP-0 energy of an xyz file (offline, in the venv)."""
    return mace_eval(xyz_path)[0]


def read_optimized():
    assert OPTIMIZED_PATH.is_file(), f"{OPTIMIZED_PATH} not found (L1)"
    return read_xyz(OPTIMIZED_PATH)


def max_displacement_aligned(init_positions, final_positions):
    """Largest atomic displacement from initial after removing rigid-body motion.

    Kabsch least-squares rotation aligns the final structure onto the initial
    one (translation is removed by centering), so a cheat that merely rigidly
    translates the input geometry shows ~0 displacement and fails.
    """
    init = np.asarray(init_positions, dtype=float)
    fin = np.asarray(final_positions, dtype=float)
    ic = init - init.mean(axis=0)
    fc = fin - fin.mean(axis=0)
    h = fc.T @ ic
    u, _s, vt = np.linalg.svd(h)
    if np.linalg.det(vt.T @ u.T) < 0.0:  # proper rotation only (det = +1)
        u[:, -1] *= -1.0
    rot = vt.T @ u.T
    aligned = fc @ rot.T
    return float(np.linalg.norm(aligned - ic, axis=1).max())


# ---- L1 Artifact ----------------------------------------------------------
def test_l1_result_exists_and_schema():
    data = load()
    # The instruction promises "exactly this schema": reject any extra or
    # missing top-level key.
    expected = {"molecule", "method", "converged", "optimized_energy_ev"}
    got = set(data.keys())
    assert got == expected, (
        f"top-level keys must be exactly {sorted(expected)} (L1), got {sorted(got)}"
    )
    assert isinstance(data["molecule"], str) and data["molecule"], "molecule not str (L1)"
    assert isinstance(data["method"], str) and data["method"], "method not str (L1)"
    assert isinstance(data["converged"], bool), "converged must be a bool (L1)"
    assert isinstance(data["optimized_energy_ev"], (int, float)) \
        and not isinstance(data["optimized_energy_ev"], bool), \
        "optimized_energy_ev not numeric (L1)"
    assert math.isfinite(float(data["optimized_energy_ev"])), "optimized_energy_ev not finite (L1)"


def test_l1_optimized_xyz_readable():
    atoms = read_optimized()
    assert atoms["n_atoms"] == 3, "optimized.xyz must contain 3 atoms (L1)"


# ---- L3 Identity ----------------------------------------------------------
def test_l3_identity():
    data = load()
    assert data["molecule"] == "SO2", f"wrong molecule (L3): {data['molecule']}"
    assert normalize_method(data["method"]) == normalize_method(CANONICAL_METHOD), (
        f"wrong method (L3): {data['method']}"
    )
    assert data["converged"] is True, f"optimization not converged (L3): {data['converged']}"


# ---- L4 Consistency (re-run MACE on the delivered optimized.xyz) -----------
def test_l4_energy_consistent_with_geometry():
    data = load()
    sp_energy, _ = mace_eval(OPTIMIZED_PATH)  # finite -> L2 execution OK
    diff = abs(sp_energy - float(data["optimized_energy_ev"]))
    assert diff < L4_TOL_EV, (
        f"optimized_energy_ev inconsistent with optimized.xyz (L4): "
        f"reported {data['optimized_energy_ev']}, single-point {sp_energy}, "
        f"diff {diff:.2e} eV"
    )


# ---- L5 Scientific --------------------------------------------------------
def test_l5_energy_in_physical_range():
    data = load()
    E = float(data["optimized_energy_ev"])
    assert L5_MIN_EV <= E <= L5_MAX_EV, f"energy out of physical MACE band (L5): {E}"


def test_l5_composition_preserved():
    atoms = read_optimized()
    counts = Counter(atoms["symbols"])
    assert counts == {"S": 1, "O": 2}, f"composition not preserved (L5): {dict(counts)}"


def test_l5_geometry_was_optimized():
    init = read_xyz(INITIAL_PATH)
    fin = read_optimized()
    maxdisp = max_displacement_aligned(init["positions"], fin["positions"])
    # Initial SO2 -> MACE minimum moves the O atoms by ~0.033 A (S ~5e-5 A); a
    # "result" that merely echoes the input geometry, or rigidly translates it
    # and runs a single point, shows ~0 rigid-body-free displacement and fails.
    assert maxdisp > 1e-3, (
        f"optimized geometry did not move from input after rigid-body removal (L5): "
        f"maxdisp={maxdisp:.5f} A"
    )


def test_l5_max_force_converged():
    # True BFGS fmax=0.01 convergence => max|force| on the delivered geometry
    # must be below 0.01 eV/A.  The initial/translated SO2 carries ~1.5 eV/A and
    # a fresh single-point-only structure is nowhere near a stationary point, so
    # this is the core "a real optimization ran and converged" proof.
    _energy, fmax = mace_eval(OPTIMIZED_PATH)
    assert fmax < FMAX_CONV_EV_ANG, (
        f"optimized geometry not force-converged (L5): "
        f"max_i ||F_i||={fmax:.5f} eV/A >= {FMAX_CONV_EV_ANG:.2f}"
    )


# ---- L6 Reference agreement ----------------------------------------------
def test_l6_energy_matches_reference():
    data = load()
    diff = abs(float(data["optimized_energy_ev"]) - REF_ENERGY)
    assert diff < REF_TOL_EV, (
        f"optimized_energy_ev off reference by {diff:.4f} eV (L6): "
        f"got {data['optimized_energy_ev']}, ref {REF_ENERGY}"
    )
