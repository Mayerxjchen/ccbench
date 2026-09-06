# E-gate geopt evidence (E1-E3)

Status: **closed** for the smoke run (2026-08-10). E1-E3 each now carry
real-run evidence from the smoke oracle (container `034-oracle-smoke`,
profile=smoke, `AI2KIT_GEO_OPT_MAX_ITER=30`).

## E1 geopt source re-executes in-container
- `solution/expert/01-geopt/run.sh` generated `coord_n_cell.inc` from
  `/app/water64.xyz` (192 atoms, cell 12.4^3), wrote `geopt.inp`
  (MAX_ITER=30), and submitted via pseudo-slurm.
- Live evidence: container.out stage 01 transcript, job id 2.

## E2 geopt scientific validity (real run, not "looks fine")
`reference/runtime-evidence/e-geopt/geopt-output.log` is the FULL CP2K output
from the smoke run. Independent numbers (re-grepped, not from the board):
- `PROGRAM ENDED` count: 1 (clean termination)
- OPT iterations recorded: 30, final OPT energy `-1102.0003422751` Ha
  (trajectory low `-1102.1487` Ha at step 28; BFGS oscillation within ~0.14 Ha)
- SCF run converged: 31 (every SCF block converged to DIIS/OT tolerance)
- RMS gradient on final step: `0.0237` Ha/bohr (draft threshold for the FULL
  expert run is far tighter; smoke profile intentionally stops at MAX_ITER=30)
- Relaxation from initial structure: `-1101.2703 -> -1102.1487` Ha
  (≈ 0.878 Ha ≈ 23.9 eV geometry relaxation; 192 atoms in 12.4 A box)
- `water64_geopt-pos-1.xyz` (30 frames) + `water64_geopt-1.restart` +
  `water64_geopt-BFGS.Hessian` all present in the container output dir.

> Honesty note: the smoke geopt reaches MAX_ITER=30 by design; it is a
> HARNESS PROOF, not a scientific-equilibrium structure. E2's *scientific*
> bar (tight convergence) is carried by the frozen expert trajectory
> (reference/expert-trajectory/geopt, 394 steps, final -1102.5766 Ha) and
> will be re-validated by the paper-profile run. What E2's real-run gate
> proves HERE is that the pipeline executes end-to-end with clean CP2K
> termination, SCF convergence, and a relaxed structure written to disk.

## E3 reproducibility seed
- `water64_geopt-1.restart` + `water64_geopt-BFGS.Hessian` are frozen. A
  re-run launched from `-RESTART water64_geopt-1.restart` with the same
  `geopt.inp` must continue the trajectory from step 30 -> full convergence.
- Full E3 (deterministic re-run -> identical trajectory) is marked on the
  board as a formal gate to be satisfied by a second paper-profile run;
  the restart/Hessian artifacts needed for that re-run are now captured.

## Re-runnable
```
# from the container:  AI2KIT_GEO_OPT_MAX_ITER=30 bash solution/expert/run.sh
# (stage 01 only; the smoke oracle already proves stage 01 -> 02 handoff)
```
