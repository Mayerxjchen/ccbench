# 03-active-learning — ai2-kit closed-loop learning

The active-learning stage implements the expert's `train -> explore -> screen ->
label -> convert` loop (the hidden lineage's approach, not its exact schedule)
on top of the 02-aimd mother set.

## Layout

```
03-active-learning/
├── run.sh
├── README.md
├── workflow/
│   ├── setup.sh                     # sample mother set -> dp-init-data + lammps-data
│   ├── iter-classic-dp-lammps-cp2k.sh  # one full AL round
│   └── prod-dp-lammps.sh            # production MLP run utility (optional)
└── config/                          # templates (copied to $AI2KIT_CFG_DIR at run time)
    ├── deepmd/{input.json,run.sh,slurm-header.sh}
    ├── lammps/{lammps.in,run.sh,slurm-header.sh}
    └── cp2k/{cp2k.inp,run.sh,slurm-header.sh}
```

## How a round works (`iter-classic-dp-lammps-cp2k.sh`)

1. **Train** — `omb combo` generates `MODEL_NUM` model dirs with unique seeds;
   `add_file_set DP_DATASET` unions `dp-init-data/*` with every previous
   `iter-*/new-dataset/*` (so the training set **grows** each round). Each model
   runs `dp train` -> `dp freeze` -> `dp compress` as a pseudo-slurm job.
2. **Explore** — `omb combo` builds LAMMPS jobs (`job-{TEMP}K-{i:03d}`) using the
   new `compress.pb` committee; `dump.lammpstrj` + `model_devi.out` are kept for
   screening.
3. **Screen** — `ai2-kit tool model_devi` grades every frame into
   good/decent/poor; `decent.xyz` is the acquisition set. Empty decent -> round
   stops (converged).
4. **Label** — up to `MAX_LABEL` decent frames are written as CP2K `coord_n_cell.inc`
   snippets and each is run through real CP2K single-point (`RUN_TYPE ENERGY_FORCE`),
   producing genuine `output` files with energies + forces.
5. **Convert** — `ai2-kit tool dpdata read` converts the CP2K outputs into
   `iter-*/new-dataset/` (DeepMD set dirs: `type.raw`, `coord.npy`,
   `energy.npy`, `force.npy`), which the next round's training consumes.

All jobs go through pseudo-slurm (`omb job slurm submit ... --wait`); the 
generated `.slurm` headers are the `config/*/slurm-header.sh` templates with
`@ENV_SH@` (absolute path to `solution/expert/env.sh`) substituted by `run.sh`.

## Knobs (set by env.sh, overridable per stage)

`MODEL_NUM`, `TRAIN_STEPS`, `DECAY_STEPS`, `NUMB_TEST`, `DISP_FREQ`, `SAVE_FREQ`,
`MD_STEPS`, `MD_TEMP`, `SAMPLE_FREQ`, `MODEL_DEVI_COND`, `DEVI_SLICE`, `MAX_LABEL`,
`AL_ROUNDS`, `SETUP_SAMPLE`, `LAMMPS_SAMPLE`.

## Idempotency

`setup.done`, `deepmd/setup.done`, `lammps/setup.done`, `screening.done`,
`cp2k/setup.done`, per-model `train.done`/`lammps.done`/`cp2k.done`,
per-round `iter.done`, and finally `al.done`.
