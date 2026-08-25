"""029-name2gibbs-co2 — hidden evaluator (hardened).

Six layers (acceptance_standard.md G6):
  L1 Artifact     /app/result.json exists, parseable, EXACT schema
                  (top-level keys == {molecule, temperature, pressure_pa, method,
                   enthalpy_ev, entropy_ev_per_k, gibbs_free_energy_ev};
                   extra keys FAIL);
                  /app/optimized.xyz exists, standard-XYZ parseable, C:1 O:2
  L2 Execution    independent GFN2-xTB recompute on /app/optimized.xyz reproduces
                  the reported H/S/G within 0.05 eV (anti-forgery: hardcoded
                  reference values with an un-optimized geometry cannot reproduce)
  L3 Identity     molecule == "CO2", method exactly identifies GFN2-xTB,
                  temperature == 800.0 (exact), pressure_pa == 101325.0 (exact)
  L4 Consistency  three-quantity consistency: G == H - T*S with reported T
  L5 Scientific   S>0, G<H, G==H-TS, magnitudes physical; delivered geometry
                  force-converged (max_i ||F_i|| < 0.01 eV/A)
  L6 Reference    H/G/S agree with regenerated reference within tolerance

Reference values are frozen from reference/regenerated_reference.json.
"""
import json
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
from ase.io import read
from ase.vibrations import Vibrations
from ase.thermochemistry import IdealGasThermo
from tblite.ase import TBLite

RESULT_PATH = Path("/app/result.json")
XYZ_PATH = Path("/app/optimized.xyz")
CANONICAL_METHOD = "GFN2-xTB"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())

# ---- frozen regenerated reference (027, GFN2-xTB @800K, pinned image) ------
REF_H = -279.847979091694
REF_S = 0.0026755155798422942
REF_G = -281.98839155556783
REF_TOL = 0.05  # eV — scientific tolerance (original-vs-regenerated diff ~1e-5; reproducibility ~1e-9)

# ---- independent-recompute tolerances --------------------------------------
TOL_RECOMPUTE = 0.05  # eV — recomputed (from delivered geometry) vs result.json
FORCE_TOL = 0.01      # eV/A — delivered geometry must be force-converged

# ---- workflow constants (mirror generate_reference.py) ---------------------
TEMPERATURE = 800.0    # K
PRESSURE = 101325.0    # Pa
SYMMETRY_NUMBER = 2    # CO2 linear -> D∞h rotational symmetry number = 2


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
_RECOMPUTE = None


def _recompute():
    """Independent GFN2-xTB thermochemistry on /app/optimized.xyz.

    Mirrors the pinned reference workflow exactly (single-point + all-3N-mode
    Vibrations + IdealGasThermo with SVD linear-geometry detection). Does NOT
    re-optimize — it recomputes from the delivered geometry, so a delivered
    geometry that was never optimized (large forces) fails the force check and
    its recomputed H/S/G will not match forged reference values.
    """
    global _RECOMPUTE
    if _RECOMPUTE is None:
        atoms = read(str(XYZ_PATH))
        atoms.calc = TBLite(method="GFN2-xTB")
        forces = np.array(atoms.get_forces())
        max_force = float(np.sqrt((forces ** 2).sum(axis=1)).max())
        E = float(atoms.get_potential_energy())
        with tempfile.TemporaryDirectory(prefix="cg_revib_") as td:
            vib = Vibrations(atoms, name=os.path.join(td, "vib"))
            vib.run()
            energies = vib.get_energies()
        linear = is_linear_molecule(atoms)
        geometry = "linear" if linear else "nonlinear"
        thermo = IdealGasThermo(
            vib_energies=energies,
            potentialenergy=E,
            atoms=atoms,
            geometry=geometry,
            symmetrynumber=SYMMETRY_NUMBER,
            spin=0.0,
        )
        recomputed = {
            "enthalpy_ev": float(thermo.get_enthalpy(temperature=TEMPERATURE)),
            "entropy_ev_per_k": float(
                thermo.get_entropy(temperature=TEMPERATURE, pressure=PRESSURE)
            ),
            "gibbs_free_energy_ev": float(
                thermo.get_gibbs_energy(temperature=TEMPERATURE, pressure=PRESSURE)
            ),
        }
        _RECOMPUTE = (recomputed, max_force)
    return _RECOMPUTE


# ---- L1 Artifact ----------------------------------------------------------
def test_l1_result_exists_and_exact_schema():
    data = load()
    # exact schema — extra top-level keys FAIL (anti-fuzz / anti-decoration)
    assert set(data.keys()) == {
        "molecule", "temperature", "pressure_pa", "method",
        "enthalpy_ev", "entropy_ev_per_k", "gibbs_free_energy_ev",
    }, f"top-level keys must be exactly the documented schema, got {sorted(data.keys())} (L1)"
    assert isinstance(data["molecule"], str) and data["molecule"].strip(), \
        "molecule must be a non-empty string (L1)"
    assert isinstance(data["method"], str) and data["method"].strip(), \
        "method must be a non-empty string (L1)"
    for prop in ("temperature", "pressure_pa", "enthalpy_ev",
                 "entropy_ev_per_k", "gibbs_free_energy_ev"):
        assert isinstance(data.get(prop), (int, float)), f"{prop} not numeric (L1)"


def test_l1_optimized_xyz_exists_and_parseable():
    assert XYZ_PATH.is_file(), f"{XYZ_PATH} not found (L1)"
    atoms = read(str(XYZ_PATH))  # must parse as standard XYZ
    assert len(atoms) == 3, f"optimized.xyz atom count != 3: {len(atoms)} (L1)"
    comp = Counter(atoms.get_chemical_symbols())
    assert comp.get("C") == 1 and comp.get("O") == 2, \
        f"optimized.xyz composition wrong: {dict(comp)} (L1)"


# ---- L2 Execution (independent recomputation) -----------------------------
def test_l2_recomputed_thermo_matches_result():
    """Fresh GFN2-xTB thermochemistry on /app/optimized.xyz must reproduce the
    reported H/S/G within 0.05 eV.

    This is the anti-forgery core: hardcoding the reference H/S/G while
    delivering a geometry that was never optimized (e.g. the un-optimized input)
    fails here because the recomputed thermochemistry of the delivered geometry
    will not match the reported values.
    """
    data = load()
    recomputed, _ = _recompute()
    for prop in ("enthalpy_ev", "entropy_ev_per_k", "gibbs_free_energy_ev"):
        got = data[prop]
        r = recomputed[prop]
        assert abs(got - r) < TOL_RECOMPUTE, (
            f"{prop} recomputed on optimized.xyz differs from reported (L2): "
            f"recomputed {r:.6f} vs reported {got:.6f}"
        )


# ---- L3 Identity ----------------------------------------------------------
def test_l3_molecule_identity():
    data = load()
    assert data["molecule"] == "CO2", f"wrong molecule (L3): {data['molecule']}"
    # exact temperature — no int() truncation (403.9 must not pass as 400)
    assert abs(float(data["temperature"]) - 800.0) < 1e-6, \
        f"wrong temperature (L3): {data['temperature']}"


def test_l3_method_requires_gfn2_xtb():
    data = load()
    assert normalize_method(data["method"]) == normalize_method(CANONICAL_METHOD), (
        f"wrong method (L3): {data['method']}"
    )


def test_l3_pressure_exact():
    data = load()
    P = float(data["pressure_pa"])
    assert abs(P - 101325.0) < 1.0, f"pressure_pa != 101325 Pa (L3): {P}"


# ---- L4 Consistency (three-quantity, temperature relation) ---------------
def test_l4_three_quantity_consistency():
    data = load()
    T = float(data["temperature"])
    H, S, G = data["enthalpy_ev"], data["entropy_ev_per_k"], data["gibbs_free_energy_ev"]
    # The three reported quantities must satisfy G = H - T*S with the *reported* T.
    assert abs(G - (H - T * S)) < 1e-3, (
        f"G != H - T*S at reported T (L4): G={G}, H-T*S={H - T * S}"
    )
    # H - G = T*S must be positive and use exactly the reported temperature scale.
    assert T * S > 0, f"H-G = T*S must be > 0 (L4): T*S={T * S}"


# ---- L5 Scientific --------------------------------------------------------
def test_l5_thermo_physical_consistency():
    data = load()
    T = float(data["temperature"])
    H, S, G = data["enthalpy_ev"], data["entropy_ev_per_k"], data["gibbs_free_energy_ev"]
    # S > 0 — pure electronic energy (S=0) fails this
    assert S > 1e-6, f"entropy must be > 0 for a real thermo calc (L5), got {S}"
    assert S < 0.01, f"entropy magnitude implausible (L5), got {S}"
    # G == H - T*S — internal anti-cheat; forged triplets rarely satisfy this
    assert abs(G - (H - T * S)) < 1e-3, f"G != H - T*S (L5)"
    assert G < H, f"Gibbs must be below enthalpy at T>0 (L5): G={G}, H={H}"


def test_l5_magnitudes_physical():
    data = load()
    H, G = data["enthalpy_ev"], data["gibbs_free_energy_ev"]
    # CO2 GFN2-xTB: H ~ -279.85 eV, G ~ -281.99 eV. Wide scientific band.
    assert -300.0 <= H <= -260.0, f"H out of chemical range (L5): {H}"
    assert -300.0 <= G <= -260.0, f"G out of chemical range (L5): {G}"
    # thermal correction must be well under the electronic binding scale
    assert 0.5 <= (H - G) <= 5.0, f"H-G = T*S implausible (L5): {H - G}"


def test_l5_optimized_geometry_force_converged():
    """The delivered geometry must be force-converged (max_i ||F_i|| < 0.01 eV/A).

    Thermochemistry is only meaningful at a (near-)equilibrium geometry; an
    un-optimized input structure carries non-zero forces and would give
    non-reference H/S/G, so this also rejects forged geometry artifacts.
    """
    _, max_force = _recompute()
    assert max_force < FORCE_TOL, (
        f"delivered geometry not force-converged: max_i ||F_i|| = {max_force:.5f} "
        f"eV/A (L5, threshold {FORCE_TOL})"
    )


# ---- L6 Reference agreement ----------------------------------------------
def test_l6_match_reference():
    data = load()
    got = {"H": data["enthalpy_ev"], "S": data["entropy_ev_per_k"],
           "G": data["gibbs_free_energy_ev"]}
    ref = {"H": REF_H, "S": REF_S, "G": REF_G}
    for name in ("H", "S", "G"):
        assert abs(got[name] - ref[name]) < REF_TOL, (
            f"{name} off reference by {abs(got[name]-ref[name]):.4f} eV (L6): "
            f"got {got[name]}, ref {ref[name]}"
        )
