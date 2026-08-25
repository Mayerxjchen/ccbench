# Water — Vibrational Frequencies (MACE-MP-0 medium)

Compute the **vibrational frequencies** of **water (H₂O)** using the **mace_mp**
calculator with the **medium-mpa-0** model and report them in **meV** and **cm⁻¹**.

Initial structure is provided in `/app/`:

```
/app/H2O.xyz
```

Perform a geometry optimization followed by a **vibrational analysis** with
`mace_mp(model="medium-mpa-0")`. H₂O is a non-linear molecule with N = 3 atoms, so it has
**3N − 6 = 3** real vibrational modes. Report only these **3 real vibrational modes**
(the 6 translational/rotational ~zero-frequency modes are not part of the physical
answer).

The delivered optimized geometry must satisfy **`max_i ||F_i|| < 0.01 eV/Å`**
under the specified calculator. ASE BFGS with `fmax=0.01` and at most 1000 steps
is the pinned reference workflow, but any optimizer that meets the final force
criterion is acceptable.

For each mode report:
- `energy_mev` — the vibrational energy in **meV**
- `frequency_cm1` — the vibrational frequency in **cm⁻¹**

## Output contract

Write `/app/result.json` with exactly this schema:

```json
{
  "molecule": "H2O",
  "method": "mace_mp / medium-mpa-0",
  "vibrational_modes": [
    {"energy_mev": 0.0, "frequency_cm1": 0.0}
  ]
}
```

(`0.0` 是占位值——填入你的真实计算结果。)

`vibrational_modes` must contain **exactly 3 modes**, sorted by increasing frequency.

Also save the **optimized geometry** to `/app/optimized.xyz` (standard XYZ format,
3 atoms: 1 O and 2 H). This is a required deliverable.

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the MACE-MP-0 medium model (`mace_mp`, `model="medium-mpa-0"`,
`device="cpu"`, `default_dtype="float64"`) is installed with its weights baked
in (no download needed). ASE `Vibrations(...).run()` is the pinned vibration
workflow, but another genuine MACE-MP-0 vibrational implementation is
acceptable if it produces the required physical modes from a geometry that
meets the force criterion above.
