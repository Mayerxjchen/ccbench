#!/usr/bin/env python3
"""Regenerate the 026 reference: MACE-MP-0 medium vibrational frequencies of H2O.

Faithfully mirrors the source ``run_ase_core`` "vib" driver
(``the source implementation ase_core.py``) so the regenerated reference stays
consistent with the original implementation:

  - calculator       : ``mace_mp(model="medium-mpa-0", device="cpu",
                                 default_dtype="float64")`` (MACE-MP-0 medium,
                                 weights baked into the image)
  - geometry opt     : BFGS, fmax=0.01 eV/A, max 1000 steps (ASEInputSchema
                       defaults: optimizer=bfgs, fmax=0.01, steps=1000)
  - Vibrations       : ``Vibrations(atoms, name=...).run()``
  - vib energies     : ``vib.get_energies()`` (eV; complex for imaginary modes)
  - mode filtering   : ``_vibrational_mode_indices`` — non-linear H2O (N=3)
                       drops the first 6 translational/rotational modes and
                       reports the 3N-6 = 3 real modes (indices 6,7,8)
  - per-mode report  : ``is_imag = abs(e.imag) > 1e-8``
                       ``energy_meV = 1e3 * (e.imag if is_imag else e.real)``
                       ``freq_cm1 = e_val / units.invcm``, suffix "i" if imaginary

Usage (from task root, inside dftworld-base-mace):

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
from ase import units
from ase.io import read
from ase.optimize import BFGS
from ase.vibrations import Vibrations
from mace.calculators import mace_mp

ROOT = pathlib.Path(__file__).resolve().parent.parent  # task root
PUBLIC = ROOT / "public"

FMAX = 0.01  # eV/A  (ASEInputSchema default)
STEPS = 1000

# ---- pinning (audit trail) -------------------------------------------------
IMAGE = "dftworld-base-mace"
# docker inspect dftworld-base-mace --format '{{.Id}}' ; update if image rebuilt
IMAGE_DIGEST = "sha256:c54103db3c6bf58a2427833361910a34715f643fa3f7aa28a081aa5672170c77"
SOFTWARE = {p: _md.version(p) for p in ("ase", "numpy", "torch", "mace-torch")}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_sha(p) -> str:
    return _sha(pathlib.Path(p).read_bytes())


def make_pinning(input_hashes: dict, raw_artifact: dict, opt_xyz: str | None = None) -> dict:
    pin = {
        "image": IMAGE,
        "image_digest": IMAGE_DIGEST,
        "software": SOFTWARE,
        "input_xyz_sha256": input_hashes,
        "raw_artifact_sha256": _sha(json.dumps(raw_artifact, sort_keys=True, default=str).encode()),
    }
    if opt_xyz:
        pin["optimized_xyz_sha256"] = file_sha(opt_xyz)
    return pin


# --------------------------------------------------------------------------- #
# helpers — mirror ase_core.py
# --------------------------------------------------------------------------- #

def is_linear_molecule(positions, tol=1e-3):
    """Mirror ase_core.is_linear_molecule (SVD singular-value ratio)."""
    coords = np.array(positions)
    centered = coords - np.mean(coords, axis=0)
    _, s, _ = np.linalg.svd(centered)
    if s[0] == 0:
        return False  # degenerate — all atoms at one point
    return (s[1] / s[0]) < tol


def vibrational_mode_indices(positions, total_modes):
    """Mirror ase_core._vibrational_mode_indices (non-linear -> drop first 6)."""
    num_atoms = len(positions)
    expected_modes = 3 * num_atoms
    if total_modes != expected_modes:
        raise ValueError(
            f"Expected {expected_modes} normal modes for {num_atoms} atoms, "
            f"got {total_modes}."
        )
    if num_atoms == 1:
        return []
    num_nonvibrational = 5 if is_linear_molecule(positions) else 6
    return list(range(num_nonvibrational, total_modes))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    xyz = PUBLIC / "H2O.xyz"
    print(f"=== H2O vibrational analysis (MACE-MP-0 medium) ===", flush=True)

    atoms = read(str(xyz))
    atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")

    # 1) geometry optimization (source driver runs opt before vib)
    if len(atoms) > 1:
        dyn = BFGS(atoms)
        dyn.run(fmax=FMAX, steps=STEPS)
    single_point_energy = float(atoms.get_potential_energy())
    print(f"  optimized energy = {single_point_energy:.8f} eV", flush=True)

    # 2) vibrational analysis
    with tempfile.TemporaryDirectory(prefix="cg_vib_") as td:
        vib = Vibrations(atoms, name=os.path.join(td, "vib"))
        vib.run()
        all_energies = vib.get_energies()

    print(f"  all 3N energies ({len(all_energies)}):")
    for i, e in enumerate(all_energies):
        print(f"    mode {i}: {1e3 * (e.imag if abs(e.imag) > 1e-8 else e.real):+.6f} meV",
              flush=True)

    # 3) mode filtering — non-linear H2O: drop first 6, keep 3N-6 = 3 modes
    positions = np.array(atoms.positions)
    mode_indices = vibrational_mode_indices(positions, len(all_energies))

    modes = []
    for mode_index in mode_indices:
        e = all_energies[mode_index]
        is_imag = bool(abs(e.imag) > 1e-8)
        e_val = e.imag if is_imag else e.real
        energy_meV = 1e3 * e_val
        freq_cm1 = e_val / units.invcm
        suffix = "i" if is_imag else ""
        modes.append({
            "mode_index": mode_index,
            "energy_mev": float(energy_meV),
            "energy_mev_str": f"{energy_meV}{suffix}",
            "frequency_cm1": float(freq_cm1),
            "frequency_cm1_str": f"{freq_cm1}{suffix}",
            "imaginary": is_imag,
        })
        print(f"  mode {mode_index}: {energy_meV}{suffix} meV   "
              f"{freq_cm1}{suffix} cm-1", flush=True)

    result = {
        "molecule": "H2O",
        "method": "mace_mp / medium-mpa-0",
        "calculator": {
            "calculator_type": "mace_mp",
            "model": "medium-mpa-0",
            "device": "cpu",
            "default_dtype": "float64",
        },
        "optimizer": {"name": "BFGS", "fmax": FMAX, "steps": STEPS},
        "geometry": "nonlinear",
        "num_real_modes": len(modes),
        "single_point_energy_eV": single_point_energy,
        "vibrational_modes": modes,
    }

    # save the optimized geometry the vib analysis was computed on (audit artifact)
    opt_xyz = ROOT / "reference" / "optimized.xyz"
    with open(opt_xyz, "w") as fh:
        fh.write(f"{len(atoms)}\nH2O optimized (MACE-MP-0 medium)\n")
        for atom in atoms:
            fh.write("%s %.10f %.10f %.10f\n" % (atom.symbol, *atom.position))
    raw_artifacts = {
        "single_point_energy_eV": single_point_energy,
        "all_vib_energies_mev": [
            1e3 * (e.imag if abs(e.imag) > 1e-8 else e.real) for e in all_energies
        ],
    }
    result["pinning"] = make_pinning(
        input_hashes={p.name: file_sha(p) for p in sorted(PUBLIC.iterdir()) if p.suffix == ".xyz"},
        raw_artifact=raw_artifacts,
        opt_xyz=str(opt_xyz),
    )

    out = ROOT / "reference" / "regenerated_reference.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
