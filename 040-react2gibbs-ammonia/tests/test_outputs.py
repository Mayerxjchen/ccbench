"""040-react2gibbs-ammonia — hidden evaluator (hardened).

Six layers (acceptance_standard.md G6):
  L1 Artifact     /app/result.json exists, parseable, EXACT schema
                  (top-level keys == {reaction, temperature, pressure_pa, method,
                   species, reaction_gibbs_energy_ev}; each species ==
                   {enthalpy_ev, gibbs_free_energy_ev, entropy_ev_per_k};
                   extra keys FAIL);
                  each of the 3 delivered optimized_*.xyz exists, standard-XYZ
                  parseable, correct stoichiometry
  L2 Execution    independent GFN2-xTB recompute on each optimized_*.xyz
                  reproduces the reported species G and the reaction ΔG within
                  0.05 eV (anti-forgery: hardcoded reference values with an
                  un-optimized geometry cannot reproduce)
  L3 Identity     reaction string, method exactly identifies GFN2-xTB,
                  temperature == 400.0 (exact), pressure_pa == 101325.0 (exact)
  L4 Consistency  reaction_gibbs_energy_ev == recomputed from reported species G
  L5 Scientific   S>0, G<H, G==H-TS, dG in range; each delivered geometry
                  force-converged (max_i ||F_i|| < 0.01 eV/A)
  L6 Reference    species H/G/S and ΔG agree with regenerated reference

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
CANONICAL_METHOD = "GFN2-xTB"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())

# ---- frozen regenerated reference (030, GFN2-xTB @400K, pinned image) ------
REF_DG = -2.1625000830744767
REF_TOL_DG = 0.05  # eV — scientific tolerance (original-vs-regenerated diff ~5e-4; reproducibility ~1e-9)
REF_SPECIES = {
    "N2": {"enthalpy_ev": -156.57281295581285, "gibbs_free_energy_ev": -157.40207165888924,
           "entropy_ev_per_k": 0.002073146757690981},
    "H2": {"enthalpy_ev": -26.386794318073843, "gibbs_free_energy_ev": -26.965347446311156,
           "entropy_ev_per_k": 0.0014463828205932806},
    "NH3": {"enthalpy_ev": -119.38978735821577, "gibbs_free_energy_ev": -120.2303070404486,
            "entropy_ev_per_k": 0.0021012992055820744},
}
REF_TOL_SPECIES = 0.05  # eV per property

# ---- independent-recompute tolerances --------------------------------------
TOL_RECOMPUTE = 0.05  # eV — recomputed (from delivered geometry) vs result.json
FORCE_TOL = 0.01      # eV/A — delivered geometry must be force-converged

# ---- workflow constants (mirror generate_reference.py) ---------------------
TEMPERATURE = 400.0    # K
PRESSURE = 101325.0    # Pa
# rotational symmetry numbers (pymatgen PointGroupAnalyzer on optimized geometry)
SPECIES_SYMMETRY = {"N2": 2, "H2": 2, "NH3": 3}
SPECIES_XYZ = {n: Path(f"/app/optimized_{n}.xyz") for n in ("N2", "H2", "NH3")}
SPECIES_COMPOSITION = {
    "N2": {"N": 2},
    "H2": {"H": 2},
    "NH3": {"N": 1, "H": 3},
}
SPECIES_NAMES = ("N2", "H2", "NH3")


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


# ---- L2 independent recompute (heavy — computed once, cached per species) ---
_SPECIES_RECOMPUTE = {}


def _recompute_species(name):
    """Independent GFN2-xTB thermochemistry on /app/optimized_<name>.xyz.

    Mirrors the pinned reference workflow exactly (single-point + all-3N-mode
    Vibrations + IdealGasThermo with SVD linear-geometry detection). Does NOT
    re-optimize — it recomputes from the delivered geometry, so a delivered
    geometry that was never optimized (large forces) fails the force check and
    its recomputed G will not match forged reference values.
    """
    global _SPECIES_RECOMPUTE
    if name not in _SPECIES_RECOMPUTE:
        path = SPECIES_XYZ[name]
        assert path.is_file(), f"{path} not found (L1)"
        atoms = read(str(path))
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
            symmetrynumber=SPECIES_SYMMETRY[name],
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
        _SPECIES_RECOMPUTE[name] = (recomputed, max_force)
    return _SPECIES_RECOMPUTE[name]


def _recompute_dg():
    """Recomputed reaction Gibbs energy from the delivered optimized geometries."""
    recomputed = {n: _recompute_species(n)[0] for n in SPECIES_NAMES}
    dg = (
        2 * recomputed["NH3"]["gibbs_free_energy_ev"]
        - recomputed["N2"]["gibbs_free_energy_ev"]
        - 3 * recomputed["H2"]["gibbs_free_energy_ev"]
    )
    return float(dg)


# ---- L1 Artifact ----------------------------------------------------------
def test_l1_result_exists_and_exact_schema():
    data = load()
    # exact schema — extra top-level keys FAIL (anti-fuzz / anti-decoration)
    assert set(data.keys()) == {
        "reaction", "temperature", "pressure_pa", "method", "species",
        "reaction_gibbs_energy_ev",
    }, f"top-level keys must be exactly the documented schema, got {sorted(data.keys())} (L1)"
    assert isinstance(data["species"], dict), "species must be an object (L1)"
    assert set(data["species"]) == set(SPECIES_NAMES), \
        f"wrong species set (L1): {sorted(data['species'])}"
    for name in SPECIES_NAMES:
        sp = data["species"][name]
        assert isinstance(sp, dict), f"{name} must be an object (L1)"
        # exact schema — extra species keys FAIL
        assert set(sp.keys()) == {"enthalpy_ev", "gibbs_free_energy_ev", "entropy_ev_per_k"}, \
            f"{name} keys must be exactly the documented schema, got {sorted(sp.keys())} (L1)"
        for prop in ("enthalpy_ev", "gibbs_free_energy_ev", "entropy_ev_per_k"):
            assert isinstance(sp.get(prop), (int, float)), f"{name}.{prop} not numeric (L1)"
    assert isinstance(data["reaction_gibbs_energy_ev"], (int, float)), "dG not numeric (L1)"


def test_l1_optimized_xyz_all_exist_and_parseable():
    for name in SPECIES_NAMES:
        path = SPECIES_XYZ[name]
        assert path.is_file(), f"{path} not found (L1)"
        atoms = read(str(path))  # must parse as standard XYZ
        comp = Counter(atoms.get_chemical_symbols())
        assert dict(comp) == SPECIES_COMPOSITION[name], (
            f"{path} composition wrong: {dict(comp)} "
            f"expected {SPECIES_COMPOSITION[name]} (L1)"
        )


# ---- L2 Execution (independent recomputation) -----------------------------
def test_l2_recomputed_species_G_matches_result():
    """Fresh GFN2-xTB G on each optimized_*.xyz must reproduce the reported
    species Gibbs energy within 0.05 eV.

    This is the anti-forgery core: hardcoding the reference species G (and hence
    ΔG) while delivering geometries that were never optimized (e.g. the
    un-optimized inputs) fails here because the recomputed Gibbs energies of the
    delivered geometries will not match the reported values.
    """
    data = load()
    recomputed = {n: _recompute_species(n)[0] for n in SPECIES_NAMES}
    for name in SPECIES_NAMES:
        got = data["species"][name]["gibbs_free_energy_ev"]
        r = recomputed[name]["gibbs_free_energy_ev"]
        assert abs(got - r) < TOL_RECOMPUTE, (
            f"{name} G recomputed on optimized_{name}.xyz differs from reported (L2): "
            f"recomputed {r:.6f} vs reported {got:.6f}"
        )


def test_l2_recomputed_dg_matches_result():
    """Fresh ΔG from the delivered optimized geometries must reproduce the
    reported reaction_gibbs_energy_ev within 0.05 eV."""
    data = load()
    dg = _recompute_dg()
    assert abs(data["reaction_gibbs_energy_ev"] - dg) < TOL_RECOMPUTE, (
        f"ΔG recomputed on delivered geometries differs from reported (L2): "
        f"recomputed {dg:.6f} vs reported {data['reaction_gibbs_energy_ev']:.6f}"
    )


# ---- L3 Identity ----------------------------------------------------------
def test_l3_reaction_identity():
    data = load()
    assert data["reaction"] == "N2 + 3 H2 -> 2 NH3", \
        f"wrong reaction (L3): {data['reaction']}"
    assert set(data["species"]) == set(SPECIES_NAMES), "wrong species set (L3)"
    # exact temperature — no int() truncation (403.9 must not pass as 400)
    assert abs(float(data["temperature"]) - 400.0) < 1e-6, \
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


# ---- L5 Scientific --------------------------------------------------------
def test_l5_thermo_physical_consistency():
    data = load()
    T = float(data["temperature"])
    for name, sp in data["species"].items():
        H, S, G = sp["enthalpy_ev"], sp["entropy_ev_per_k"], sp["gibbs_free_energy_ev"]
        # S > 0 for polyatomic molecules (pure electronic energy -> S=0 fails this)
        assert S > 1e-6, f"{name}: entropy must be > 0 for a real thermo calc (L5), got {S}"
        assert S < 0.01, f"{name}: entropy magnitude implausible (L5), got {S}"
        # G == H - T*S — internal anti-cheat; forged triplets rarely satisfy this
        assert abs(G - (H - T * S)) < 1e-3, f"{name}: G != H - T*S (L5)"
        assert G < H, f"{name}: Gibbs must be below enthalpy at T>0 (L5)"


def test_l5_dg_in_chemical_range():
    data = load()
    dG = data["reaction_gibbs_energy_ev"]
    # ammonia synthesis is exothermic but entropically unfavourable at 400K;
    # GFN2-xTB gives dG slightly negative. Wide scientific band.
    assert -10.0 <= dG <= 5.0, f"dG out of chemical range (L5): {dG}"


def test_l5_optimized_geometries_force_converged():
    """Each delivered geometry must be force-converged (max_i ||F_i|| < 0.01 eV/A).

    Thermochemistry is only meaningful at (near-)equilibrium geometries; the
    un-optimized input structures carry non-zero forces (e.g. N2 ~4.3 eV/A,
    H2 ~1.2 eV/A) and would give non-reference G, so this also rejects forged
    geometry artifacts.
    """
    for name in SPECIES_NAMES:
        _, max_force = _recompute_species(name)
        assert max_force < FORCE_TOL, (
            f"optimized_{name}.xyz not force-converged: max_i ||F_i|| = {max_force:.5f} "
            f"eV/A (L5, threshold {FORCE_TOL})"
        )


# ---- L4 Consistency -------------------------------------------------------
def test_l4_dg_recomputed_from_species():
    data = load()
    sp = data["species"]
    recomputed = (
        2 * sp["NH3"]["gibbs_free_energy_ev"]
        - sp["N2"]["gibbs_free_energy_ev"]
        - 3 * sp["H2"]["gibbs_free_energy_ev"]
    )
    assert abs(recomputed - data["reaction_gibbs_energy_ev"]) < 1e-6, (
        f"reaction_gibbs_energy_ev != stoichiometric recomputation (L4): "
        f"{data['reaction_gibbs_energy_ev']} vs {recomputed}"
    )


# ---- L6 Reference agreement ----------------------------------------------
def test_l6_species_match_reference():
    data = load()
    for name, ref in REF_SPECIES.items():
        for prop, ref_val in ref.items():
            got = data["species"][name][prop]
            assert abs(got - ref_val) < REF_TOL_SPECIES, (
                f"{name}.{prop} off reference by {abs(got-ref_val):.4f} eV (L6): "
                f"got {got}, ref {ref_val}"
            )


def test_l6_dg_match_reference():
    data = load()
    assert abs(data["reaction_gibbs_energy_ev"] - REF_DG) < REF_TOL_DG, (
        f"dG off reference by {abs(data['reaction_gibbs_energy_ev']-REF_DG):.4f} eV (L6): "
        f"got {data['reaction_gibbs_energy_ev']}, ref {REF_DG}"
    )
