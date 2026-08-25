Calculate the single-point energy of a water molecule using CP2K.

A starter input is at `/app/H2O.inp` with **`CUTOFF 200` Ry**, which is **not** sufficient for the accuracy target below.

Read `/app/convergence_guide.txt`. It lists plane-wave cutoff tests versus a 500 Ry reference. Your job is to **choose the smallest integer `CUTOFF` (Ry)** that meets the stated accuracy target, not to guess a generic “large enough” value.

Requirements:
- Update `&MGRID` / `CUTOFF` in `/app/H2O.inp` (or a copy you run from `/app`).
- Write your choice as a single line `CUTOFF=<integer>` to `/app/params.txt` (example: `CUTOFF=320`).
- Run: `/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out`
- Extract the final total energy (Hartree) and write **only** the numeric value to `/app/out.txt`.

Do not change the functional, basis sets, coordinates, or cell beyond what is already in the template.
