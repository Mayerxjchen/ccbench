"""Pinned MatClaw electric-field calculator for CIPS."""

from __future__ import annotations

import numpy as np
from ase.calculators.calculator import Calculator, all_changes


CHARGES_E = {"Cu": 0.765, "In": -0.085, "P": -0.085, "S": -0.085}


class UniformElectricForce(Calculator):
    implemented_properties = ["energy", "forces"]

    def __init__(self, efield_charges, field, **kwargs):
        super().__init__(**kwargs)
        self.efield_charges = dict(efield_charges)
        self.field = np.asarray(field, dtype=float)

    def calculate(self, atoms=None, properties=("energy", "forces"), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)
        charges = np.asarray([self.efield_charges[s] for s in atoms.get_chemical_symbols()])
        self.results["forces"] = charges[:, None] * self.field[None, :]
        self.results["energy"] = float(-np.sum(charges * (atoms.positions @ self.field)))
