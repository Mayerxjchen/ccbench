# Ammonia Synthesis — Gibbs Free Energy of Reaction (GFN2-xTB)

Compute the **Gibbs free energy of reaction** for ammonia synthesis at **400 K** using
**GFN2-xTB** (the TBLite calculator) and report it in **eV**.

The balanced reaction is:

```
N2 + 3 H2 -> 2 NH3
```

Initial structures are provided in `/app/`:

```
/app/N2.xyz
/app/H2.xyz
/app/NH3.xyz
```

For **each** of the three species, perform a GFN2-xTB **thermochemistry** calculation at
400 K and 1 atm — i.e. geometry optimization followed by vibrational analysis and
ideal-gas thermochemistry — to obtain the species Gibbs free energy `G`. Then combine the
species Gibbs free energies according to the stoichiometry:

```
dG = 2·G(NH3) − G(N2) − 3·G(H2)
```

Every delivered optimized geometry must satisfy
**`max_i ||F_i|| < 0.01 eV/Å`** under the specified calculator. ASE BFGS with
`fmax=0.01` and at most 1000 steps is the pinned reference workflow, but any
optimizer that meets the final force criterion is acceptable.

## Output contract

Write `/app/result.json` with exactly this schema:

```json
{
  "reaction": "N2 + 3 H2 -> 2 NH3",
  "temperature": 400,
  "pressure_pa": 101325,
  "method": "GFN2-xTB",
  "species": {
    "N2": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0},
    "H2": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0},
    "NH3": {"enthalpy_ev": 0.0, "gibbs_free_energy_ev": 0.0, "entropy_ev_per_k": 0.0}
  },
  "reaction_gibbs_energy_ev": 0.0
}
```

Also save the **optimized geometry of each of the 3 species** to
`/app/optimized_N2.xyz`, `/app/optimized_H2.xyz` and `/app/optimized_NH3.xyz`
(standard XYZ format). These are required deliverables.

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the TBLite (GFN2-xTB) calculator is installed. A
`Vibrations`/`IdealGasThermo` workflow is the pinned thermochemistry workflow,
but another implementation is acceptable if it produces genuine GFN2-xTB
thermochemistry from geometries that meet the force criterion above.
