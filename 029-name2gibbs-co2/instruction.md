# Carbon Dioxide — Thermochemistry (Gibbs Free Energy) at 800 K (GFN2-xTB)

Compute the **thermochemical properties** (enthalpy, entropy, Gibbs free energy) of
**carbon dioxide (CO₂)** at **800 K** using **GFN2-xTB** (the TBLite calculator) and
report the Gibbs free energy in **eV**.

Initial structure is provided in `/app/`:

```
/app/CO2.xyz
```

Perform a GFN2-xTB **thermochemistry** calculation at 800 K and 1 atm — i.e. geometry
optimization followed by vibrational analysis and ideal-gas thermochemistry — to obtain
the molecular enthalpy `H`, entropy `S`, and Gibbs free energy `G`.

The delivered optimized geometry must satisfy **`max_i ||F_i|| < 0.01 eV/Å`**
under the specified calculator. ASE BFGS with `fmax=0.01` and at most 1000 steps
is the pinned reference workflow, but any optimizer that meets the final force
criterion is acceptable.

## Output contract

Write `/app/result.json` with exactly this schema:

```json
{
  "molecule": "CO2",
  "temperature": 800,
  "pressure_pa": 101325,
  "method": "GFN2-xTB",
  "enthalpy_ev": 0.0,
  "entropy_ev_per_k": 0.0,
  "gibbs_free_energy_ev": 0.0
}
```

Also save the **optimized geometry** to `/app/optimized.xyz` (standard XYZ format,
3 atoms: 1 C and 2 O). This is a required deliverable.

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the TBLite (GFN2-xTB) calculator is installed. A
`Vibrations`/`IdealGasThermo` workflow is the pinned thermochemistry workflow,
but another implementation is acceptable if it produces genuine GFN2-xTB
thermochemistry from a geometry that meets the force criterion above.
