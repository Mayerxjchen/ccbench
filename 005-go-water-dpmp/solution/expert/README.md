# 042 solution/expert — container-adapted DPMP workflow (hidden lineage)

The oracle implements the paper's four fixed stages, container-executable via
the pseudo-slurm contract (real job lifecycle; `#SBATCH` headers kept, HPC
conda-activate/module paths dropped).

| stage | dir | what it does |
|---|---|---|
| 1 reference labeling | `01-aimd/` | CP2K revPBE-D3 AIMD (NVT 300 K, 1 fs, H mass 2.014) on a supplied initial structure -> labeled DeepMD raw set |
| 2 initial training | `02-train/` | deepmd-jax DPMP training (`deepmd_jax.train.train`) on the generated labels |
| 3 iterative improvement | `03-active-learning/` | jax-md explore -> model-deviation screen -> CP2K label -> retrain (>=1 closed round) |
| 4 validation | `04-validation/` | E/F accuracy (`deepmd_jax.train.test`), NVT stability (`deepmd_jax.md.Simulation`), water density profile |

## env gate (smoke vs formal)

```bash
export AI2KIT_042_PROFILE=smoke   # short: few AIMD steps, few train steps, 1 AL round
export AI2KIT_042_PROFILE=formal  # full: paper-scale schedules
```

`run.sh` reads `AI2KIT_042_PROFILE` and sets per-stage budgets. The smoke run
only proves the machinery; it never passes formal grading (the verifier rejects
a smoke-profile submission under `profile=formal`).

## layout

- `run.sh` orchestrates 01 -> 02 -> 03 -> 04, then `manifest_writer.py` writes
  `final/manifest.json`.
- Every remote job goes through `sbatch` (pseudo-slurm); the sandbox is only the
  control layer.
- type_map `[O, H, C]`, mass `[15.999, 1.008, 12.011]`, rcut 6.0.
