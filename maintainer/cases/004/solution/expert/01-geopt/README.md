# 01-geopt — structure relaxation

**Hidden-lineage mapping:** `reference/expert-trajectory/geopt/`.

**Why:** the public `water64.xyz` is an unrelaxed PACKMOL random fill with close
contacts; running CP2K AIMD directly on it is unstable (SCF can struggle and the
trajectory can blow up). The expert path relaxes it first (GEO_OPT, BFGS).

**What runs:**

1. `tools/xyz2cp2k_inc.py` converts the supplied structure into a CP2K
   `coord_n_cell.inc` (`&CELL` + `&COORD`, 12.4 Å cubic box, frame 0).
2. `geopt.inp` (BLYP-D3 / TZV2P-GTH, CUTOFF 500, same DFT settings as AIMD and
   the labeling runs) is generated with `MAX_ITER` from `$AI2KIT_GEO_OPT_MAX_ITER`.
3. The job is `sbatch`-ed through the pseudo-slurm scheduler and awaited with
   `../wait_job.sh` (polling `sacct`).
4. The optimised trajectory `water64_geopt-pos-1.xyz` is harvested; its last
   frame becomes the AIMD starting structure.

**Container adaptations from the HPC original:**

- `@include /public/home/<site-user>/ai2kit/.../coord_n_cell.inc` → relative
  `@include coord_n_cell.inc` (the job runs in the output dir where the file is
  symlinked).
- `module load cp2k/2024.3` + conda boilerplate → `source $EXPERT_DIR/env.sh`.
- `MAX_ITER 600` → env-gated (paper 200, smoke 30).
- Output path moved from `/public/.../data/cp2k-geopt/output` to
  `$AI2KIT_GEOPT_DIR/output`.

**Idempotency:** marker `geopt.done`; re-running skips the job.
