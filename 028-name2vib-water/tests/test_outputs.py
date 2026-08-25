"""028-name2vib-water — hidden evaluator (hardened).

Six layers (acceptance_standard.md G6):
  L1 Artifact     /app/result.json exists, parseable, EXACT schema
                  (top-level keys == {molecule, method, vibrational_modes};
                   each mode == {energy_mev, frequency_cm1}; extra keys FAIL);
                  /app/optimized.xyz exists, standard-XYZ parseable, O:1 H:2
  L2 Execution    independent MACE-MP-0 medium Vibrations recompute on
                  /app/optimized.xyz reproduces the reported 3 modes within
                  1 meV / 8 cm-1 (anti-forgery: hardcoded reference modes with a
                  wrong geometry cannot reproduce -> FAIL)
  L3 Identity     molecule == "H2O", method exactly identifies mace_mp / medium-mpa-0
  L4 Consistency  energy_mev and frequency_cm1 satisfy the unit relation
                  (1 cm^-1 = 0.1239842 meV); the L2 recompute is an additional
                  cross-check against the delivered geometry
  L5 Scientific   exactly 3 modes (3N-6 for non-linear H2O), 0 < freq < 4500
                  cm^-1, sorted ascending; delivered geometry force-converged
                  (max_i ||F_i|| < 0.01 eV/A) so the vibration analysis is meaningful
  L6 Reference    each mode agrees with the regenerated reference within
                  tolerance (1 meV = 8 cm^-1)

Reference values are frozen from reference/regenerated_reference.json.
"""
import json
import math
import os
import sys
import tempfile
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
from ase import units
from ase.io import read
from ase.vibrations import Vibrations
from mace.calculators import mace_mp

RESULT_PATH = Path("/app/result.json")
XYZ_PATH = Path("/app/optimized.xyz")
CANONICAL_METHOD = "mace_mp / medium-mpa-0"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())

# ---- frozen regenerated reference (026, MACE-MP-0 medium, pinned image) ----
REF_ENERGY_MEV = [207.54494811634504, 461.8154197982689, 484.1391752707933]
REF_FREQ_CM1 = [1673.9629120054237, 3724.7925904761423, 3904.8458225920867]
REF_TOL_MEV = 1.0   # meV — tolerance (original-vs-regenerated diff ~0.12 meV)
REF_TOL_CM1 = 8.0   # cm^-1 = 1 meV

# 1 cm^-1  == 0.1239842 meV  (ASE units.invcm = 1.23984193e-4 eV)
MEV_PER_CM1 = 0.1239842

# delivered geometry must be force-converged (BFGS fmax=0.01) for the
# vibration analysis to be meaningful
FORCE_TOL = 0.01  # eV/A


# ---- L1 helpers -----------------------------------------------------------
def load():
    assert RESULT_PATH.is_file(), f"{RESULT_PATH} not found (L1)"
    data = json.loads(RESULT_PATH.read_text())
    assert isinstance(data, dict), "result.json must be a JSON object (L1)"
    return data


def is_linear_molecule(atoms, tol=1e-3):
    """Mirror ase_core.is_linear_molecule (SVD singular-value ratio)."""
    positions = np.array(atoms.positions)
    centered = positions - np.mean(positions, axis=0)
    _, s, _ = np.linalg.svd(centered)
    if s[0] == 0:
        return False  # degenerate
    return (s[1] / s[0]) < tol


# ---- L2 independent recompute (heavy — computed once, cached) ---------------
_CALC = None


def _calc():
    global _CALC
    if _CALC is None:
        _CALC = mace_mp(model="medium-mpa-0", device="cpu",
                        default_dtype="float64")
    return _CALC


_RECOMPUTE = None


def _recompute():
    """Independent MACE-MP-0 medium vibrational analysis on /app/optimized.xyz.

    Returns (recomputed_modes, max_force_eV_per_Ang) where recomputed_modes is
    a list of (energy_mev, frequency_cm1) for the 3N-6 = 3 real modes, sorted
    ascending by frequency. Mirrors the pinned reference workflow: non-linear
    H2O (N=3) drops the first 6 translational/rotational modes and keeps the
    3N-6 = 3 real modes.
    """
    global _RECOMPUTE
    if _RECOMPUTE is None:
        atoms = read(str(XYZ_PATH))
        atoms.calc = _calc()
        forces = np.array(atoms.get_forces())
        max_force = float(np.linalg.norm(forces, axis=1).max())
        with tempfile.TemporaryDirectory(prefix="cg_revib_") as td:
            vib = Vibrations(atoms, name=os.path.join(td, "vib"))
            vib.run()
            all_energies = vib.get_energies()
        n = len(atoms)
        num_nonvib = 5 if is_linear_molecule(atoms) else 6
        modes = []
        for mi in range(num_nonvib, 3 * n):
            e = all_energies[mi]
            is_imag = bool(abs(e.imag) > 1e-8)
            e_val = e.imag if is_imag else e.real
            modes.append((float(1e3 * e_val), float(e_val / units.invcm)))
        modes.sort(key=lambda m: m[1])
        _RECOMPUTE = (modes, max_force)
    return _RECOMPUTE


# ---- L1 Artifact ----------------------------------------------------------
def test_l1_result_exists_and_exact_schema():
    data = load()
    # exact schema — extra top-level keys FAIL (anti-fuzz / anti-decoration)
    assert set(data.keys()) == {"molecule", "method", "vibrational_modes"}, (
        f"top-level keys must be exactly {{molecule, method, vibrational_modes}}, "
        f"got {sorted(data.keys())} (L1)"
    )
    assert isinstance(data["molecule"], str) and data["molecule"].strip(), \
        "molecule must be a non-empty string (L1)"
    assert isinstance(data["method"], str) and data["method"].strip(), \
        "method must be a non-empty string (L1)"
    modes = data["vibrational_modes"]
    assert isinstance(modes, list), "vibrational_modes must be an array (L1)"
    assert len(modes) > 0, "vibrational_modes must be non-empty (L1)"
    for m in modes:
        assert isinstance(m, dict), "each mode must be an object (L1)"
        # exact schema — extra mode keys FAIL
        assert set(m.keys()) == {"energy_mev", "frequency_cm1"}, (
            f"mode keys must be exactly {{energy_mev, frequency_cm1}}, "
            f"got {sorted(m.keys())} (L1)"
        )
        for prop in ("energy_mev", "frequency_cm1"):
            assert isinstance(m.get(prop), (int, float)), \
                f"mode.{prop} not numeric (L1): {m.get(prop)}"


def test_l1_optimized_xyz_exists_and_parseable():
    assert XYZ_PATH.is_file(), f"{XYZ_PATH} not found (L1)"
    atoms = read(str(XYZ_PATH))  # must parse as standard XYZ
    assert len(atoms) == 3, f"optimized.xyz atom count != 3: {len(atoms)} (L1)"
    comp = Counter(atoms.get_chemical_symbols())
    assert comp.get("O") == 1 and comp.get("H") == 2, \
        f"optimized.xyz composition wrong: {dict(comp)} (L1)"


# ---- L2 Execution (independent recomputation) -----------------------------
def test_l2_independent_vibration_recompute_matches():
    """Fresh MACE-MP-0 medium Vibrations on /app/optimized.xyz must reproduce
    the reported 3 modes within 1 meV / 8 cm-1.

    This is the anti-forgery core: hardcoding the reference mode values while
    delivering a wrong geometry (e.g. the un-optimized input) fails here because
    the recomputed frequencies of the delivered geometry will not match the
    reported ones.
    """
    data = load()
    reported = sorted(data["vibrational_modes"],
                      key=lambda m: float(m["frequency_cm1"]))
    assert len(reported) == 3

    recomputed, _ = _recompute()
    assert len(recomputed) == 3, (
        f"expected 3 real modes from recomputation on optimized.xyz, "
        f"got {len(recomputed)} (L2)"
    )
    for i, (r_mev, r_cm1) in enumerate(recomputed):
        d_mev = abs(r_mev - float(reported[i]["energy_mev"]))
        d_cm1 = abs(r_cm1 - float(reported[i]["frequency_cm1"]))
        assert d_mev < REF_TOL_MEV and d_cm1 < REF_TOL_CM1, (
            f"mode {i} recomputed on optimized.xyz differs from reported (L2): "
            f"recomputed {r_mev:.4f} meV / {r_cm1:.4f} cm-1 vs reported "
            f"{reported[i]['energy_mev']} meV / {reported[i]['frequency_cm1']} cm-1"
        )


# ---- L3 Identity ----------------------------------------------------------
def test_l3_molecule_identity():
    data = load()
    assert data["molecule"] == "H2O", f"wrong molecule (L3): {data['molecule']}"


def test_l3_method_requires_mace_medium():
    data = load()
    assert normalize_method(data["method"]) == normalize_method(CANONICAL_METHOD), (
        f"wrong method (L3): {data['method']}"
    )


# ---- L4 Consistency (unit relation) --------------------------------------
def test_l4_mev_cm1_unit_consistency():
    data = load()
    for i, m in enumerate(data["vibrational_modes"]):
        e_mev = float(m["energy_mev"])
        f_cm1 = float(m["frequency_cm1"])
        # energy_mev == frequency_cm1 * 0.1239842  (hard unit identity)
        assert abs(e_mev - f_cm1 * MEV_PER_CM1) < 0.05, (
            f"mode {i}: energy_mev and frequency_cm1 inconsistent with "
            f"1 cm^-1 == 0.1239842 meV (L4): {e_mev} vs {f_cm1 * MEV_PER_CM1}"
        )


# ---- L5 Scientific --------------------------------------------------------
def test_l5_three_real_modes():
    data = load()
    modes = data["vibrational_modes"]
    assert len(modes) == 3, (
        f"H2O is non-linear with N=3 -> exactly 3N-6 = 3 real modes (L5), "
        f"got {len(modes)}"
    )


def test_l5_frequencies_physical_and_sorted():
    data = load()
    freqs = [float(m["frequency_cm1"]) for m in data["vibrational_modes"]]
    energies = [float(m["energy_mev"]) for m in data["vibrational_modes"]]
    for f, e in zip(freqs, energies):
        assert 0.0 < f < 4500.0, f"frequency out of physical range (L5): {f} cm-1"
        assert 0.0 < e < 560.0, f"energy out of physical range (L5): {e} meV"
    # ASE returns modes in ascending order; a genuine vib run is sorted
    assert freqs == sorted(freqs), f"modes not sorted by frequency (L5): {freqs}"
    assert energies == sorted(energies), f"modes not sorted by energy (L5): {energies}"


def test_l5_optimized_geometry_force_converged():
    """The delivered geometry must be force-converged (max_i ||F_i|| < 0.01 eV/A).

    Vibration frequencies are only meaningful at a (near-)equilibrium geometry;
    an un-optimized or translated input structure carries non-zero forces and
    would give non-reference frequencies, so this also rejects forged geometry
    artifacts.
    """
    _, max_force = _recompute()
    assert max_force < FORCE_TOL, (
        f"delivered geometry not force-converged: max_i ||F_i|| = {max_force:.5f} "
        f"eV/A (L5, threshold {FORCE_TOL})"
    )


# ---- L6 Reference agreement ----------------------------------------------
def test_l6_modes_match_reference():
    data = load()
    modes = sorted(data["vibrational_modes"], key=lambda m: float(m["frequency_cm1"]))
    assert len(modes) == 3
    for i, m in enumerate(modes):
        d_mev = abs(float(m["energy_mev"]) - REF_ENERGY_MEV[i])
        d_cm1 = abs(float(m["frequency_cm1"]) - REF_FREQ_CM1[i])
        assert d_mev < REF_TOL_MEV, (
            f"mode {i} energy off reference by {d_mev:.4f} meV (L6): "
            f"got {m['energy_mev']}, ref {REF_ENERGY_MEV[i]}"
        )
        assert d_cm1 < REF_TOL_CM1, (
            f"mode {i} frequency off reference by {d_cm1:.4f} cm-1 (L6): "
            f"got {m['frequency_cm1']}, ref {REF_FREQ_CM1[i]}"
        )
