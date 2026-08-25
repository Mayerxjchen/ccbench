# Sulfur Dioxide — Geometry Optimization (MACE-MP-0 medium)

Run a **geometry optimization** for **sulfur dioxide (SO₂)** and report its
optimized energy using the **mace_mp** calculator with the **medium-mpa-0**
model.

The initial structure is provided in `/app/`:

```
/app/SO2.xyz
```

Perform a **geometry optimization** (energy minimization) of SO₂ from this
initial structure with the MACE-MP-0 medium model and report the **optimized
total energy in eV**. Save the optimized structure as `/app/optimized.xyz`.

The delivered optimized geometry must satisfy **`max_i ||F_i|| < 0.01 eV/Å`**
under the specified calculator. ASE BFGS with `fmax=0.01` and at most 1000 steps
is the pinned reference workflow, but any optimizer that meets the final force
criterion is acceptable.

## Output contract

Write `/app/result.json` with exactly this schema:

```json
{
  "molecule": "SO2",
  "method": "mace_mp / medium-mpa-0",
  "converged": true,
  "optimized_energy_ev": 0.0
}
```

- `molecule`: the chemical formula of the optimized system (`SO2`).
- `method`: must be exactly `"mace_mp / medium-mpa-0"`.
- `converged`: whether the geometry optimization converged to the force
  threshold.
- `optimized_energy_ev`: the potential energy of the **optimized** structure in
  eV (a genuine MACE-MP-0 result — no hard-coded numbers).

Also write the optimized structure to `/app/optimized.xyz` (standard XYZ).

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the MACE (`mace_mp`) calculator is installed. The MACE-MP-0 medium weights
are bundled offline in the package, so the calculator loads without any
download:

```python
from mace.calculators import mace_mp
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
```

The optimizer implementation may differ from the reference workflow, but the
reported energy and delivered geometry must be genuine MACE-MP-0 results and
must satisfy the force criterion above.
