# Sulfur Dioxide — Geometry Optimization + Structure File (MACE-MP-0)

Run a **geometry optimization** for **sulfur dioxide (SO₂)** using the **MACE-MP**
machine-learning potential (`mace_mp`, model `medium-mpa-0`, device `cpu`,
`default_dtype=float64`) and report the optimized energy in **eV**.

Initial structure is provided in `/app/`:

```
/app/SO2.xyz
```

The delivered optimized geometry must satisfy **`max_i ||F_i|| < 0.01 eV/Å`**
under the specified calculator. ASE BFGS with `fmax=0.01` and at most 1000 steps
is the pinned reference workflow, but any optimizer that meets the final force
criterion is acceptable.

## Output contract

1. **Save the optimized structure** — the final converged geometry — as a **standard
   XYZ file** at `/app/optimized.xyz`. It must be parseable by standard XYZ readers
   (e.g. `ase.io.read('/app/optimized.xyz')`) and must contain the same molecule
   (3 atoms: 1 S + 2 O).

2. Write `/app/result.json` with exactly this schema:

```json
{
  "molecule": "SO2",
  "method": "mace_mp / medium-mpa-0",
  "converged": true,
  "optimized_energy_ev": 0.0,
  "structure_file": "optimized.xyz"
}
```

## Environment

Use the bundled interpreter directly at `/app/.venv/bin/python`. ASE 3.25.0
with the MACE foundation-model calculator is installed and the model weights
are pre-baked (no download needed):

```python
from mace.calculators import mace_mp
atoms.calc = mace_mp(model="medium-mpa-0", device="cpu", default_dtype="float64")
```

Write the converged final structure to `/app/optimized.xyz`; the optimizer may
differ from the pinned reference workflow if the final force criterion is met.
