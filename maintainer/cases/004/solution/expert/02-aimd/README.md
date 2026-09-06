# 02-aimd — CP2K AIMD reference data

Generates the first-principles reference data for DeePMD training from the
**optimised** geopt structure.

## Pipeline (`run.sh`)

1. `coord_n_cell.inc` is generated from the last frame of the optimised geopt
   trajectory: `tools/xyz2cp2k_inc.py --frame last --cell 12.4`.
2. `aimd.inp` placeholders are substituted (`@AIMD_STEPS@`, `@AIMD_TRAJ_EACH@`,
   `@WALLTIME@`) and the CP2K AIMD job is submitted to pseudo-slurm:
   `sbatch --parsable` -> `wait_job.sh` polls `sacct` until COMPLETED/FAILED.
3. Raw CP2K outputs are verified: `water64_aimd-pos-1.xyz`,
   `water64_aimd-frc-1.xyz`, `water64_aimd-1.cell`, `water64_aimd-1.ener`
   (prefix = CP2K `PROJECT_NAME`).
4. `convert.py` (self-contained port of the archived `tools/cp2k2extxyz.py`)
   converts raw outputs into the filtered, labeled mother set
   `$AI2KIT_CFG_DIR/aimd.xyz` (drops step 0, dedupes duplicate steps, applies the
   temperature window; never pads).
5. A filter report `processed/filter.tsv` and `processed/provenance.json` are
   written; marker `aimd.done` closes the stage.

## Outputs

| path | content |
|---|---|
| `output/water64_aimd-pos-1.xyz` | CP2K coordinate trajectory (192 atoms, header `i = step, time, E` in Hartree) |
| `output/water64_aimd-frc-1.xyz` | CP2K force trajectory |
| `output/water64_aimd-1.{cell,ener}` | box + energy/temperature per step |
| `config/aimd.xyz` | labeled extxyz mother set for 03-active-learning |
| `processed/filter.tsv` | per-frame keep/drop reason |

## Container adaptation vs the HPC record

- `module load cp2k/2024.3` -> `source env.sh` + `mpirun -np 8 cp2k.psmp`.
- The reference `input/coord_n_cell.inc` (hard-coded in the archived record) is
  instead generated from the *optimised* geopt output, so the AIMD starts from a
  physically sane, relaxed structure (verifier L2).
