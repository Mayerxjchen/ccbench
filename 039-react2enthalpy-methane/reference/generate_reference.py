#!/usr/bin/env python3
"""Regenerate the 029 reference: GFN2-xTB thermochemistry for CH4/O2/CO2/H2O.

Faithfully mirrors the source ``run_ase`` "thermo" driver
(``the source implementation ase_core.py``) so the regenerated reference stays
consistent with the original implementation:

  - geometry optimization : BFGS, fmax=0.01 eV/A, max 1000 steps
  - Vibrations            : ``Vibrations(atoms, name=...).run()``
  - vib energies          : ``vib.get_energies()`` (eV; complex for imaginary modes)
  - thermochemistry       : ``IdealGasThermo(vib_energies=all_energies, ...)``
                            (source passes ALL 3N modes, incl. the ~zero
                            translational/rotational ones — reproduced as-is)
  - symmetry number       : pymatgen PointGroupAnalyzer
  - spin S                : 0 (multiplicity defaults to 1 in the source path)
  - P                     : 101325 Pa (source default pressure)

Usage (from task root, inside dftworld-base-xtb):

    source /app/.venv/bin/activate
    python3 reference/generate_reference.py

Writes ``reference/regenerated_reference.json``.
"""
import hashlib
import importlib.metadata as _md
import json
import os
import pathlib
import tempfile

import numpy as np
from ase.io import read
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from ase.thermochemistry import IdealGasThermo
from tblite.ase import TBLite

ROOT = pathlib.Path(__file__).resolve().parent.parent  # task root
PUBLIC = ROOT / "public"

FMAX = 0.01  # eV/A  (source default)
STEPS = 1000
PRESSURE = 101325.0  # Pa (source default)

# ---- pinning (audit trail) -------------------------------------------------
IMAGE = "dftworld-base-xtb"
# docker inspect dftworld-base-xtb --format '{{.Id}}' ; update if image rebuilt
IMAGE_DIGEST = "sha256:4e1b395ae86f20738d78ccc446146b43dd89d4e0771091998b07859f0a86eb1d"
SOFTWARE = {p: _md.version(p) for p in ("ase", "numpy", "tblite", "pymatgen")}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_sha(p) -> str:
    return _sha(pathlib.Path(p).read_bytes())


def make_pinning(input_hashes: dict, raw_artifact: dict, opt_xyz: dict | None = None) -> dict:
    pin = {
        "image": IMAGE,
        "image_digest": IMAGE_DIGEST,
        "software": SOFTWARE,
        "input_xyz_sha256": input_hashes,
        "raw_artifact_sha256": _sha(json.dumps(raw_artifact, sort_keys=True, default=str).encode()),
    }
    if opt_xyz:
        pin["optimized_xyz_sha256"] = {k: file_sha(v) for k, v in opt_xyz.items()}
    return pin


# --------------------------------------------------------------------------- #
# helpers — mirror ase_core.py
# --------------------------------------------------------------------------- #

def is_linear_molecule(atoms, tol=1e-3):
    coords = np.array(atoms.positions)
    centered = coords - np.mean(coords, axis=0)
    _, s, _ = np.linalg.svd(centered)
    if s[0] == 0:
        return False
    return (s[1] / s[0]) < tol


def get_symmetry_number(atoms):
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.symmetry.analyzer import PointGroupAnalyzer
    aaa = AseAtomsAdaptor()
    molecule = aaa.get_molecule(atoms)
    return PointGroupAnalyzer(molecule).get_rotational_symmetry_number()


def species_thermo(xyz_path, temperature, opt_xyz_path=None):
    """One species: opt -> vibrations -> ideal-gas thermo. Returns dict."""
    atoms = read(str(xyz_path))
    atoms.calc = TBLite(method="GFN2-xTB")

    if len(atoms) > 1:
        dyn = BFGS(atoms)
        dyn.run(fmax=FMAX, steps=STEPS)
        if opt_xyz_path:
            with open(opt_xyz_path, "w") as fh:
                fh.write(f"{len(atoms)}\n{pathlib.Path(xyz_path).stem} optimized (GFN2-xTB)\n")
                for atom in atoms:
                    fh.write("%s %.10f %.10f %.10f\n" % (atom.symbol, *atom.position))
    single_point_energy = float(atoms.get_potential_energy())

    if len(atoms) == 1:  # source implementation special-cases single atoms
        return {
            "energy_ev": single_point_energy,
            "enthalpy_ev": single_point_energy,
            "entropy_ev_per_k": 0.0,
            "gibbs_free_energy_ev": single_point_energy,
            "geometry": "nonlinear",
            "symmetry_number": 1,
        }

    with tempfile.TemporaryDirectory(prefix="cg_vib_") as td:
        vib = Vibrations(atoms, name=os.path.join(td, "vib"))
        vib.run()
        all_energies = vib.get_energies()

    linear = is_linear_molecule(atoms)
    geometry = "linear" if linear else "nonlinear"
    sig = get_symmetry_number(atoms)

    thermo = IdealGasThermo(
        vib_energies=all_energies,
        potentialenergy=single_point_energy,
        atoms=atoms,
        geometry=geometry,
        symmetrynumber=sig,
        spin=0.0,
    )
    return {
        "energy_ev": single_point_energy,
        "enthalpy_ev": float(thermo.get_enthalpy(temperature=temperature)),
        "entropy_ev_per_k": float(
            thermo.get_entropy(temperature=temperature, pressure=PRESSURE)
        ),
        "gibbs_free_energy_ev": float(
            thermo.get_gibbs_energy(temperature=temperature, pressure=PRESSURE)
        ),
        "geometry": geometry,
        "symmetry_number": sig,
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    T = 400.0
    species_files = {
        "CH4": PUBLIC / "CH4.xyz",
        "O2": PUBLIC / "O2.xyz",
        "CO2": PUBLIC / "CO2.xyz",
        "H2O": PUBLIC / "H2O.xyz",
    }

    species = {}
    opt_paths = {}
    for name, f in species_files.items():
        print(f"=== {name} ({f.name}) @ {T} K ===", flush=True)
        opt_paths[name] = ROOT / "reference" / f"optimized_{name}.xyz"
        species[name] = species_thermo(f, T, opt_xyz_path=opt_paths[name])
        r = species[name]
        print(f"  H = {r['enthalpy_ev']:.6f}  G = {r['gibbs_free_energy_ev']:.6f}  "
              f"S = {r['entropy_ev_per_k']:.8f}  geom={r['geometry']}  σ={r['symmetry_number']}",
              flush=True)

    # Stoichiometric aggregation (the evaluator recomputes this independently).
    dH = (
        species["CO2"]["enthalpy_ev"]
        + 2 * species["H2O"]["enthalpy_ev"]
        - species["CH4"]["enthalpy_ev"]
        - 2 * species["O2"]["enthalpy_ev"]
    )

    result = {
        "reaction": "CH4 + 2 O2 -> CO2 + 2 H2O",
        "temperature": T,
        "pressure_pa": PRESSURE,
        "method": "GFN2-xTB",
        "species": {k: {kk: vv for kk, vv in v.items()} for k, v in species.items()},
        "reaction_enthalpy_ev": float(dH),
    }
    result["pinning"] = make_pinning(
        input_hashes={p.name: file_sha(p) for p in sorted(PUBLIC.iterdir()) if p.suffix == ".xyz"},
        raw_artifact={"species": species, "reaction_enthalpy_ev": float(dH)},
        opt_xyz={k: str(v) for k, v in opt_paths.items()},
    )

    out = ROOT / "reference" / "regenerated_reference.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nΔH(400 K) = {dH:.6f} eV")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
