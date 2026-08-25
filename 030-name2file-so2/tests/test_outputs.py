"""030-name2file-so2 — hidden evaluator.

Layers (acceptance_standard.md G6):
  L1 Artifact     /app/result.json exists, parseable, *exact* schema
                  {molecule, method, converged, optimized_energy_ev,
                   structure_file}; /app/optimized.xyz exists, standard-XYZ
                  parseable, 3 atoms, S:1 O:2
  L2 Execution    MACE opt really ran: converged==true, energy finite,
                  optimized geometry differs from the provided initial geometry
                  after removing rigid-body motion (Kabsch)
  L3 Identity     molecule=="SO2", method exactly identifies mace_mp / medium-mpa-0,
                  structure_file=="optimized.xyz"
  L4 Consistency  fresh MACE-MP-0 single-point energy on /app/optimized.xyz ==
                  result.json optimized_energy_ev (anti-forgery: a copied input
                  structure would fail this because its energy is ~0.048 eV higher)
  L5 Scientific   optimized energy physically plausible (~ -16.8 eV);
                  SO2 bond lengths / bond angle sane (molecule intact);
                  max|force| on optimized.xyz < 0.01 eV/A (the BFGS fmax
                  criterion — proves the geometry really is converged, not a
                  translated single-point echo)
  L6 Reference    optimized_energy_ev agrees with regenerated reference within tolerance

Reference values are frozen from reference/regenerated_reference.json.
"""
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

# The verifier runs with the venv python pinned by the Harness
# (VERIFIER_PYTHON=/opt/dftworld/venv/bin/python, outside /root and /app);
# numpy/ase and the ML calculator live in that venv.  Derive the venv
# site-packages from the pinned interpreter and inject it, so the hidden
# evaluator never depends on an interpreter or package set shipped in the
# sealed submission.
VERIFIER_PY = os.environ.get("VERIFIER_PYTHON", "/opt/dftworld/venv/bin/python")
_VENV_SP = Path(VERIFIER_PY).resolve().parent.parent / "lib/python3.12/site-packages"
if _VENV_SP.is_dir() and str(_VENV_SP) not in sys.path:
    sys.path.insert(0, str(_VENV_SP))

import numpy as np
from ase.io import read

RESULT_PATH = Path("/app/result.json")
XYZ_PATH = Path("/app/optimized.xyz")
INITIAL_XYZ = Path("/app/SO2.xyz")
CANONICAL_METHOD = "mace_mp / medium-mpa-0"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())

# ---- frozen regenerated reference (028, MACE-MP-0, pinned image) ------
REF_ENERGY = -16.815808388438125  # eV (regenerated; original was -16.815808019358535)
REF_TOL = 0.05  # eV — scientific tolerance (original-vs-regenerated diff ~4e-7; reproducibility ~1e-9)

# ---- L5 force-convergence threshold = the BFGS fmax used by the oracle -------
FMAX_CONV_EV_ANG = 0.01  # eV/A  (max |force| below this => converged)


def load():
    assert RESULT_PATH.is_file(), f"{RESULT_PATH} not found (L1)"
    data = json.loads(RESULT_PATH.read_text())
    assert isinstance(data, dict), "result.json must be a JSON object (L1)"
    return data


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


# ---- MACE energy + max-force in one model load (cached) --------------------
_MACE_CACHE = {}


def mace_energy_and_fmax(xyz_path):
    """(energy_eV, max_abs_force_eV_A) of an xyz via MACE-MP-0.

    One calculation returns both the single-point energy (L4) and the max
    |force| (L5) so the model loads only once.  Cached by path.
    """
    p = str(xyz_path)
    if p not in _MACE_CACHE:
        from mace.calculators import mace_mp

        atoms = read(p)
        atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
        energy = float(atoms.get_potential_energy())
        forces = np.asarray(atoms.get_forces())
        fmax = float(np.linalg.norm(forces, axis=1).max())
        _MACE_CACHE[p] = (energy, fmax)
    return _MACE_CACHE[p]


# ---- L1 Artifact ----------------------------------------------------------
def test_l1_result_exists_and_schema():
    data = load()
    # The instruction promises "exactly this schema": reject any extra or
    # missing top-level key.
    expected = {"molecule", "method", "converged", "optimized_energy_ev",
                "structure_file"}
    got = set(data.keys())
    assert got == expected, (
        f"top-level keys must be exactly {sorted(expected)} (L1), got {sorted(got)}"
    )
    assert isinstance(data["molecule"], str), "molecule must be a string (L1)"
    assert isinstance(data["method"], str), "method must be a string (L1)"
    assert isinstance(data["converged"], bool), "converged must be a bool (L1)"
    assert isinstance(data["optimized_energy_ev"], (int, float)) \
        and not isinstance(data["optimized_energy_ev"], bool), \
        "optimized_energy_ev must be a number, not a bool (L1)"
    assert isinstance(data["structure_file"], str), "structure_file must be a string (L1)"


def test_l1_optimized_xyz_exists_and_parseable():
    assert XYZ_PATH.is_file(), f"{XYZ_PATH} not found (L1)"
    atoms = read(str(XYZ_PATH))  # must parse as standard XYZ
    assert len(atoms) == 3, f"optimized.xyz atom count != 3: {len(atoms)} (L1)"
    comp = Counter(atoms.get_chemical_symbols())
    assert comp.get("S") == 1 and comp.get("O") == 2, \
        f"optimized.xyz composition wrong: {dict(comp)} (L1)"


def test_l1_result_refers_to_existing_xyz():
    data = load()
    assert data["structure_file"] == "optimized.xyz", \
        f"structure_file must be 'optimized.xyz', got {data['structure_file']} (L1)"
    assert XYZ_PATH.is_file(), "structure_file referenced but file missing (L1)"


# ---- L2 Execution ---------------------------------------------------------
def test_l2_real_converged_optimization():
    data = load()
    assert data["converged"] is True, "optimization did not converge (L2)"
    assert math.isfinite(float(data["optimized_energy_ev"])), \
        "optimized_energy_ev not finite (L2)"
    # the optimized structure must actually differ from the provided input,
    # after removing rigid-body motion — proves a real optimization ran rather
    # than a verbatim copy (or rigid translation) of SO2.xyz.
    init = read(str(INITIAL_XYZ))
    final = read(str(XYZ_PATH))
    d = max_displacement_aligned(init.positions, final.positions)
    assert d > 1e-3, (
        f"optimized.xyz is the input up to a rigid body motion; no optimization "
        f"ran (L2), rigid-body-free max shift {d:.2e} Å"
    )


# ---- L3 Identity ----------------------------------------------------------
def test_l3_identity():
    data = load()
    assert data["molecule"] == "SO2", f"wrong molecule (L3): {data['molecule']}"
    assert normalize_method(data["method"]) == normalize_method(CANONICAL_METHOD), (
        f"wrong method (L3): {data['method']}"
    )
    assert data["converged"] is True, "converged must be true (L3)"
    assert data["structure_file"] == "optimized.xyz", "structure_file must be optimized.xyz (L3)"


# ---- L4 Consistency: energy of the saved structure == reported energy -----
def test_l4_energy_matches_saved_structure():
    data = load()
    recomputed, _ = mace_energy_and_fmax(XYZ_PATH)
    reported = float(data["optimized_energy_ev"])
    assert abs(recomputed - reported) < 0.01, (
        f"reported energy != fresh MACE single-point on optimized.xyz (L4): "
        f"reported {reported:.6f}, recomputed {recomputed:.6f}"
    )


# ---- L5 Scientific --------------------------------------------------------
def test_l5_energy_chemical_range():
    data = load()
    E = float(data["optimized_energy_ev"])
    # SO2 with MACE-MP-0 sits near -16.8 eV; wide scientific band
    assert -30.0 <= E <= -5.0, f"optimized energy out of chemical range (L5): {E}"


def test_l5_so2_geometry_intact():
    atoms = read(str(XYZ_PATH))
    pos = atoms.positions
    syms = atoms.get_chemical_symbols()
    s_idx = [i for i, s in enumerate(syms) if s == "S"][0]
    o_idx = [i for i, s in enumerate(syms) if s == "O"]
    # S-O bond lengths ~1.45 Å (intact molecule, not dissociated)
    bonds = [np.linalg.norm(pos[o] - pos[s_idx]) for o in o_idx]
    for b in bonds:
        assert 1.2 <= b <= 1.8, f"S-O bond length implausible (L5): {b:.3f} Å"
    # O-S-O angle ~118° for bent SO2
    v1 = pos[o_idx[0]] - pos[s_idx]
    v2 = pos[o_idx[1]] - pos[s_idx]
    cosang = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
    ang = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    assert 100.0 <= ang <= 140.0, f"O-S-O angle implausible (L5): {ang:.1f} deg"


def test_l5_max_force_converged():
    # True BFGS fmax=0.01 convergence => max|force| on the delivered geometry
    # must be below 0.01 eV/A.  The initial/translated SO2 carries ~1.5 eV/A and
    # a fresh single-point-only structure is nowhere near a stationary point, so
    # this is the core "a real optimization ran and converged" proof.
    _energy, fmax = mace_energy_and_fmax(XYZ_PATH)
    assert fmax < FMAX_CONV_EV_ANG, (
        f"optimized geometry not force-converged (L5): "
        f"max_i ||F_i||={fmax:.5f} eV/A >= {FMAX_CONV_EV_ANG:.2f}"
    )


# ---- L6 Reference agreement ----------------------------------------------
def test_l6_energy_matches_reference():
    data = load()
    E = float(data["optimized_energy_ev"])
    assert abs(E - REF_ENERGY) < REF_TOL, (
        f"optimized_energy_ev off reference by {abs(E-REF_ENERGY):.4f} eV (L6): "
        f"got {E}, ref {REF_ENERGY}"
    )
