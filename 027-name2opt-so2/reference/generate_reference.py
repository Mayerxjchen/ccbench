#!/usr/bin/env python3
"""Regenerate the 025 reference: MACE-MP-0 geometry optimization for SO2.

Faithfully mirrors the source ``run_ase`` "opt" driver
(``the source implementation ase_core.py``):

  - input            : public/SO2.xyz (ase.build.molecule("SO2") geometry)
  - calculator       : mace_mp(model="medium-mpa-0", device="cpu",
                                default_dtype="float64")
  - optimizer        : BFGS (source default), fmax=0.01 eV/A, steps=1000
  - energy           : atoms.get_potential_energy() after optimization (eV)

The MACE-MP-0 medium weights are baked into the ``dftworld-base-mace`` image
(``mace/calculators/foundations_models/mace-mpa-0-medium.model``), so the
script is fully offline.

Usage (from task root, inside dftworld-base-mace):

    source /app/.venv/bin/activate
    python3 reference/generate_reference.py

Writes ``reference/regenerated_reference.json`` (and
``reference/optimized.xyz``).
"""
import hashlib
import json
import pathlib
import warnings

import importlib.metadata as _md

from ase import __version__ as ase_version
from ase.io import read, write
from ase.optimize import BFGS
from mace.calculators import mace_mp

ROOT = pathlib.Path(__file__).resolve().parent.parent  # task root
PUBLIC = ROOT / "public"
REFDIR = ROOT / "reference"

FMAX = 0.01  # eV/A (source default)
STEPS = 1000
MODEL = "medium-mpa-0"
DEFAULT_DTYPE = "float64"
DEVICE = "cpu"

# docker inspect dftworld-base-mace --format '{{.Id}}' ; update if image rebuilt
IMAGE_DIGEST = "sha256:c54103db3c6bf58a2427833361910a34715f643fa3f7aa28a081aa5672170c77"


def sha256_of_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

# MACE uses torch.load with weights_only checks; silence the noise, not errors.
warnings.filterwarnings("ignore", category=UserWarning)


def sha256_of_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    xyz = PUBLIC / "SO2.xyz"
    atoms = read(str(xyz))
    initial_positions = atoms.positions.copy()

    print(f"=== SO2 geometry optimization (mace_mp / {MODEL}) ===", flush=True)
    atoms.calc = mace_mp(model=MODEL, device=DEVICE, default_dtype=DEFAULT_DTYPE)
    dyn = BFGS(atoms)
    converged = dyn.run(fmax=FMAX, steps=STEPS)
    energy = float(atoms.get_potential_energy())

    print(f"  converged = {converged}  (nsteps = {dyn.nsteps})", flush=True)
    print(f"  optimized energy = {energy!r} eV", flush=True)

    # Persist the optimized structure next to the regenerated reference.
    out_xyz = REFDIR / "optimized.xyz"
    write(str(out_xyz), atoms)

    import mace
    import torch

    result = {
        "molecule": "SO2",
        "method": "mace_mp / medium-mpa-0",
        "converged": bool(converged),
        "nsteps": int(dyn.nsteps),
        "optimized_energy_ev": energy,
        "unit": "eV",
        "fmax_ev_per_angstrom": FMAX,
        "max_steps": STEPS,
        "initial_positions": initial_positions.tolist(),
        "optimized_positions": atoms.positions.tolist(),
        "optimized_xyz": out_xyz.name,
        "optimized_xyz_sha256": sha256_of_file(out_xyz),
        "input_xyz_sha256": sha256_of_file(xyz),
        "software": {
            "ase": ase_version,
            "mace_torch": getattr(mace, "__version__", "unknown"),
            "torch": torch.__version__,
        },
        "image": "dftworld-base-mace",
    }

    result["pinning"] = {
        "image": "dftworld-base-mace",
        "image_digest": IMAGE_DIGEST,
        "software": {"ase": ase_version, "numpy": _md.version("numpy"),
                     "torch": torch.__version__, "mace_torch": getattr(mace, "__version__", "unknown")},
        "input_xyz_sha256": sha256_of_file(xyz),
        "optimized_xyz_sha256": sha256_of_file(out_xyz),
        "raw_artifact_sha256": sha256_of_bytes(
            json.dumps({"optimized_energy_ev": energy, "nsteps": int(dyn.nsteps)},
                       sort_keys=True).encode()
        ),
    }

    out_json = REFDIR / "regenerated_reference.json"
    out_json.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {out_json}")
    print(f"wrote {out_xyz}")


if __name__ == "__main__":
    main()
