Calculate the single-point energy of a water molecule using CP2K.

The input `/app/H2O.inp` uses **`EPS_SCF 1.0E-4`**. Read the convergence table to decide whether it meets the accuracy requirement; if not, tighten it.

Read `/app/scf_convergence.txt`. It compares total energies at different `EPS_SCF` values (fixed `CUTOFF 400` Ry). Choose the **coarsest** `EPS_SCF` (largest numeric threshold) that still satisfies the target.

Allowed values to pick from: **`1.0E-4`**, **`1.0E-5`**, **`1.0E-6`**, **`1.0E-7`**.

Requirements:
- Update `&SCF` / `EPS_SCF` in `/app/H2O.inp`.
- Write `EPS_SCF=<value>` to `/app/params.txt` (example: `EPS_SCF=1.0E-6`).
- Run CP2K and write **only** the final total energy (Hartree) to `/app/out.txt`.

Reference fully converged energy (EPS_SCF=1.0E-7): **-17.219675287352594 Ha**. Target: **|E - E_ref| <= 1.0e-5 Ha**.
