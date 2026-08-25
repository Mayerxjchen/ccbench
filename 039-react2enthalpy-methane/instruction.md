# Methane Combustion — Enthalpy of Reaction (GFN2-xTB)

Compute the **enthalpy of reaction** for methane combustion at **400 K** using **GFN2-xTB**
(the TBLite calculator) and report it in **eV**.

The balanced reaction is:

```
CH4 + 2 O2 -> CO2 + 2 H2O
```

Initial structures are provided in `/app/`:

```
/app/CH4.xyz
/app/O2.xyz
/app/CO2.xyz
/app/H2O.xyz
```

For **each** of the four species, perform a GFN2-xTB **thermochemistry** calculation at
400 K and 1 atm — i.e. geometry optimization followed by vibrational analysis and
ideal-gas thermochemistry — to obtain the species enthalpy `H`. Then combine the species
enthalpies according to the stoichiometry:

```
dH = H(CO2) + 2·H(H2O) − H(CH4) − 2·H(O2)
```

## Required computational protocol (must follow — reproducibility lock)

To reproduce the pinned ChemGraph workflow exactly, these settings are **required**
(non-adherence fails evaluation):

1. **Geometry convergence** — every delivered optimized geometry must satisfy
   **`max_i ||F_i|| < 0.01 eV/Å`** under GFN2-xTB. BFGS is the pinned reference workflow, not a required optimizer.
   ASE BFGS with `fmax=0.01` and at most 1000 steps is a reproducible
   implementation; another optimizer is acceptable when the delivered geometry
   meets the same atom-wise force criterion.

2. **Electronic spin** — compute **all four species with `spin = 0` (closed shell,
   singlet), including O₂**. This reproduces the locked source pipeline. It is a
   reproducibility setting, **not** the physically recommended state for gas-phase O₂
   (whose ground state is a triplet); a triplet O₂ (`spin = 2`) is chemically more
   realistic but will **not** match the locked reference values and will fail evaluation.

## Output contract

Write `/app/result.json` with exactly this schema:

```json
{
  "reaction": "CH4 + 2 O2 -> CO2 + 2 H2O",
  "temperature": 400,
  "pressure_pa": 101325,
  "method": "GFN2-xTB",
  "species": {
    "CH4": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0},
    "O2": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0},
    "CO2": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0},
    "H2O": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0}
  },
  "reaction_enthalpy_ev": 0.0
}
```

Also save the **optimized geometry of each of the 4 species** to
`/app/optimized_CH4.xyz`, `/app/optimized_O2.xyz`, `/app/optimized_CO2.xyz` and
`/app/optimized_H2O.xyz` (standard XYZ format). These are required deliverables.

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the TBLite (GFN2-xTB) calculator is installed. A
`Vibrations`/`IdealGasThermo` workflow is the pinned thermochemistry workflow,
but another implementation is acceptable if it produces genuine GFN2-xTB
thermochemistry, preserves the required `spin = 0` setting, and delivers
geometries satisfying the force criterion above.
