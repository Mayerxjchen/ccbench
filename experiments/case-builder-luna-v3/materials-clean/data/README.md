# Calibration data (toy)

`dataset.csv` — 24 Lennard-Jones clusters (LJ-01..LJ-24).

Columns:

| column | unit | meaning |
|---|---|---|
| cluster_id | – | stable identifier |
| n_atoms | – | cluster size |
| r_min_ang | Å | first-moment min pair distance |
| r_max_ang | Å | cutoff-window max pair distance |
| coord_num_mean | – | mean coordination at 6.0 Å |
| e_distort | – | distortion index (0 = icosahedral) |
| energy_per_atom_eV | eV/atom | cohesive energy per atom |

Author's split: LJ-25..LJ-32 are held out by the original study and are NOT
in this file. License: CC-BY-4.0 (see ../LICENSE-NOTES.md).
