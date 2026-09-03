# Cohesive-Energy Prediction for Lennard-Jones Clusters with a Fixed-Feature MLP (LC-MLP v1)

> Synthetic toy ecosystem written for the v3 skill regression. Not real
> science; internally consistent enough to exercise a `model_evaluation`
> reproduction. Version: props-2026-09-03.

## Abstract

We report LC-MLP v1, a feed-forward network predicting per-atom cohesive
energy of icosahedral and decahedral Lennard-Jones clusters from five
fixed structural descriptors. On the reported split, LC-MLP v1 attains a
mean absolute error of **0.031 eV/atom** and RMSE **0.045 eV/atom**.
Checkpoint, training config, and descriptor recipe are in `repo/`; the
24-cluster calibration table is in `data/`.

## Methods

### Descriptors

Per cluster: `n_atoms`, `r_min_ang`, `r_max_ang`, `coord_num_mean`,
`e_distort` (columns of `data/dataset.csv`; units in the data README).
A distance-cutoff radius of **6.0 Å** fixes the descriptor basis.

### Model

Two hidden layers, width **32**, SiLU activations; inputs standardized
with the training-set mean/std recorded in `repo/config.yaml`; target is
`energy_per_atom_eV` (units eV/atom, zero at dissociated atoms).

### Training

Adam, lr 1e-3, 200 epochs, seed 20260903, on the 24-cluster calibration
table. The published checkpoint is `repo/weights/lc-mlp-v1.txt`.

## Reported results (Table 1)

| metric | value |
|---|---|
| MAE (eV/atom) | 0.031 |
| RMSE (eV/atom) | 0.045 |
| split | fixed, rows LJ-25..LJ-32 held out by the authors |

## Reproduction notes

The evaluation task this ecosystem supports: run the published checkpoint
over a re-serialized descriptor table and check the reproduced metric
band against Table 1. No retraining is implied.
