# LJ cluster descriptor table — `descriptors.csv`

Case value: the **re-serialized** 24-cluster calibration table
(`lj_cluster_calibration_24`), LJ-01..LJ-24, sourced from the dataset that
accompanies the LC-MLP v1 paper.

## Columns

| column                | unit    | meaning                                          |
|-----------------------|---------|--------------------------------------------------|
| `cluster_id`          | –       | stable identifier (LJ-01..LJ-24)                 |
| `n_atoms`             | –       | cluster size                                     |
| `r_min_ang`           | Å       | first-moment min pair distance                   |
| `r_max_ang`           | Å       | cutoff-window max pair distance                  |
| `coord_num_mean`      | –       | mean coordination at 6.0 Å                       |
| `e_distort`           | –       | distortion index (0 = icosahedral)               |
| `energy_per_atom_eV`  | eV/atom | cohesive energy per atom (labels)                |

The first five columns are the model's feature template in order (see
`lc-mlp-v1/config.yaml`); the last column is the label your predictions are
compared against.

## Split

The set LJ-25..LJ-32 was held out by the original study and is NOT in this
file. This case's evaluation covers the provided 24 clusters only.

## Attribution

Derived from the dataset distributed with the LC-MLP v1 paper
(CC-BY-4.0 per the ecosystem license notes).