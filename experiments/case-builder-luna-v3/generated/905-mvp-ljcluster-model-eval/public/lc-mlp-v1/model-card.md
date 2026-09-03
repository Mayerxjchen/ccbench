# LC-MLP v1 — model card (case-level)

Distilled for this case from the published configuration
(`config.yaml` in this directory) and the source paper (`paper.md`). No
information in this card goes beyond those two documents.

## Identity

- **Model**: LC-MLP v1, a fixed-feature feed-forward network.
- **Task**: per-atom cohesive energy of Lennard-Jones clusters,
  `energy_per_atom_eV` (eV/atom, zero at dissociated atoms).
- **Artifact**: published checkpoint `weights/lc-mlp-v1.txt` (toy JSON
  format).

## Architecture

- Layer type: `ffn`.
- Hidden layers: `[32, 32]`, activations `silu`.
- Distance-cutoff radius for the descriptor basis: `6.0` angstrom.

## Input feature template (order matters)

`n_atoms`, `r_min_ang`, `r_max_ang`, `coord_num_mean`, `e_distort`

Inputs are standardized before inference with the training-set statistics:

- train mean: `[-1.0, 3.02, 3.98, 4.9, 0.5]`
- train std:  `[1.0, 0.08, 0.35, 1.8, 0.3]`

## Training (for provenance only — no retraining is part of this task)

- Optimizer: `adam`, lr `0.001`, epochs `200`, seed `20260903`.
- Trained on the 24-cluster calibration table (LJ-01..LJ-24); the set
  LJ-25..LJ-32 was held out by the authors.

## Reported metrics

| metric | value |
|---|---|
| MAE (eV/atom) | 0.031 |
| RMSE (eV/atom) | 0.045 |

## Licensing / redistribution status

Per the source ecosystem license notes, the checkpoint carries **no stated
license**; treat redistribution as restricted and use the artifact only for
this evaluation.