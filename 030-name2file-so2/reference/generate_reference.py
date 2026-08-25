#!/usr/bin/env python3
"""Regenerate the 028 reference: MACE-MP-0 geometry optimization of SO2 + XYZ file.

Faithfully mirrors the source ``run_ase`` "opt" driver
(``the source implementation ase_core.py``) so the regenerated reference stays
consistent with the original implementation:

  - initial structure  : ase.build.molecule("SO2")  (matches public/SO2.xyz)
  - calculator         : mace_mp(model="medium-mpa-0", device="cpu",
                                 default_dtype="float64")
  - geometry optimization : BFGS, fmax=0.01 eV/A, max 1000 steps
  - optimized energy    : atoms.get_potential_energy() after convergence (eV)
  - artifact gate       : the optimized (final converged) geometry is written to a
                          standard XYZ file; it must be parseable by ase.io.read and
                          contain the same molecule (3 atoms: S:1, O:2)

Usage (from task root, inside dftworld-base-mace):

    source /app/.venv/bin/activate
    python3 reference/generate_reference.py

Writes ``reference/regenerated_reference.json`` and ``reference/optimized.xyz``.
"""
import hashlib
import json
import pathlib

import ase
from ase.io import read, write
from ase.optimize import BFGS
from mace.calculators import mace_mp
from importlib.metadata import version as _pkg_version

ROOT = pathlib.Path(__file__).resolve().parent.parent  # task root
PUBLIC = ROOT / "public"
REF_DIR = ROOT / "reference"

FMAX = 0.01  # eV/A  (source default)
STEPS = 1000
ORIGINAL_ENERGY_EV = -16.815808019358535  # dataset id=5 (optimization_from_name)

# docker inspect dftworld-base-mace --format '{{.Id}}' ; update if image rebuilt
IMAGE_DIGEST = "sha256:c54103db3c6bf58a2427833361910a34715f643fa3f7aa28a081aa5672170c77"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    xyz_in = PUBLIC / "SO2.xyz"
    atoms = read(str(xyz_in))
    print(f"loaded {xyz_in.name}: {len(atoms)} atoms "
          f"{sorted(atoms.get_chemical_symbols())}", flush=True)

    atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")

    dyn = BFGS(atoms)
    converged = dyn.run(fmax=FMAX, steps=STEPS)
    energy = float(atoms.get_potential_energy())
    print(f"BFGS converged={converged}  E = {energy:.12f} eV", flush=True)

    # ---- artifact gate: save the optimized geometry as a standard XYZ file ----
    xyz_out = REF_DIR / "optimized.xyz"
    write(str(xyz_out), atoms, format="xyz")

    # ---- validate the saved XYZ: parseable, 3 atoms, composition S:1 O:2 ----
    back = read(str(xyz_out))
    symbols = back.get_chemical_symbols()
    from collections import Counter

    comp = Counter(symbols)
    assert len(back) == 3, f"optimized.xyz atom count != 3: {len(back)}"
    assert comp.get("S") == 1 and comp.get("O") == 2, f"composition wrong: {dict(comp)}"
    print(f"optimized.xyz verified: 3 atoms, composition {dict(comp)}", flush=True)

    diff = abs(energy - ORIGINAL_ENERGY_EV)
    print(f"original_vs_regenerated_abs_diff = {diff:.3e} eV", flush=True)
    assert diff < 0.1, f"regenerated energy deviates from original by {diff:.4f} eV"

    result = {
        "molecule": "SO2",
        "method": "mace_mp / medium-mpa-0",
        "converged": bool(converged),
        "optimized_energy_ev": energy,
        "structure_file": "optimized.xyz",
        "original_energy_ev": ORIGINAL_ENERGY_EV,
        "original_vs_regenerated_abs_diff_ev": diff,
        "calculator": {
            "calculator_type": "mace_mp",
            "model": "medium-mpa-0",
            "device": "cpu",
            "default_dtype": "float64",
        },
        "optimizer": {"name": "BFGS", "fmax": FMAX, "steps": STEPS},
        "final_structure": {
            "symbols": symbols,
            "positions": back.positions.tolist(),
        },
        "generator_image": "dftworld-base-mace",
        "software_versions": {
            "ase": ase.__version__,
            "mace_torch": _pkg_version("mace-torch"),
            "torch": _pkg_version("torch"),
            "numpy": _pkg_version("numpy"),
        },
    }

    result["pinning"] = {
        "image": "dftworld-base-mace",
        "image_digest": IMAGE_DIGEST,
        "software": result["software_versions"],
        "input_xyz_sha256": _sha256(xyz_in),
        "optimized_xyz_sha256": _sha256(xyz_out),
        "raw_artifact_sha256": hashlib.sha256(
            json.dumps({"optimized_energy_ev": energy, "converged": bool(converged)},
                       sort_keys=True).encode()
        ).hexdigest(),
    }

    out = REF_DIR / "regenerated_reference.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nE = {energy:.12f} eV  (original {ORIGINAL_ENERGY_EV:.12f} eV, "
          f"diff {diff:.3e} eV)")
    print(f"wrote {out}")
    print(f"wrote {xyz_out}")


if __name__ == "__main__":
    main()
