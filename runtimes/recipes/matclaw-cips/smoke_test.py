"""Build-time MatClaw CIPS image gate."""

import math
import shutil
import subprocess
from pathlib import Path

import dpdata  # noqa: F401
import numpy as np
from ase import units
from ase.io import read
from ase.md.verlet import VelocityVerlet
from deepmd.calculator import DP
from deepmd.infer import DeepPot


ASSETS = Path("/opt/matclaw/assets")
MODEL = ASSETS / "frozen_model.pb"
STRUCTURE = ASSETS / "CuInP2S6.cif"


def main() -> None:
    atoms = read(STRUCTURE)
    assert len(atoms) == 10
    assert sorted(set(atoms.get_chemical_symbols())) == ["Cu", "In", "P", "S"]

    model = DeepPot(str(MODEL))
    assert model.get_type_map() == ["Cu", "In", "P", "S"]
    energy, forces, virial = model.eval(
        atoms.positions.reshape(1, -1),
        atoms.cell.array.reshape(1, 9),
        np.array([0, 1, 2, 2, 3, 3, 3, 3, 3, 3], dtype=np.int32),
    )[:3]
    assert np.isfinite(energy).all()
    assert np.isfinite(forces).all()
    assert np.isfinite(virial).all()

    lmp = shutil.which("lmp")
    assert lmp, "deepmd-kit[lmp] did not install lmp"
    style_text = subprocess.run(
        [lmp, "-log", "none", "-echo", "none"],
        input="info styles pair\n",
        check=True,
        text=True,
        capture_output=True,
    ).stdout.lower()
    assert "deepmd" in style_text, "LAMMPS lacks pair_style deepmd"

    atoms.calc = DP(model=str(MODEL))
    atoms.set_momenta(np.zeros((len(atoms), 3)))
    dynamics = VelocityVerlet(atoms, timestep=0.1 * units.fs)
    dynamics.run(2)
    assert math.isfinite(float(atoms.get_potential_energy()))


if __name__ == "__main__":
    main()
