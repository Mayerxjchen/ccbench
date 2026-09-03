# Task: Evaluate the published LC-MLP v1 checkpoint

Source relationship: paper-faithful reproduction (`paper_faithful`). The
target is the published LC-MLP v1 model; your job is to **evaluate** it, not
retrain it.

## Objective

Run the published LC-MLP v1 checkpoint over the 24-cluster Lennard-Jones
descriptor table and reproduce the reported per-atom cohesive-energy metric
band (paper Table 1: MAE 0.031 eV/atom, RMSE 0.045 eV/atom).

## Inputs you may use

- `public/dataset.csv` — the 24-cluster descriptor table (LJ-01..LJ-24).
- `public/data-README.md` — data dictionary (columns, units).
- `public/config.yaml` — model hyperparameters and preprocessing
  (standardization mean/std).
- `public/weights/lc-mlp-v1.txt` — the published checkpoint.
- `public/system.json` — machine-readable system/observable contract.
- `public/submission-schema.json` — the required submission manifest schema.

You may **not** use any held-out author test rows (LJ-25..LJ-32), any grader
answer files, or any hidden reference/solution material. Those are not
provided and must not be sought.

## What to do

1. Load the published checkpoint and the descriptor table.
2. Apply the recorded standardization (`config.yaml` `preprocessing`).
3. Predict `energy_per_atom_eV` for all 24 clusters.
4. Compute energy MAE and RMSE against the table's `energy_per_atom_eV`.
5. Report your reproduced metrics. Acceptance requires energy MAE < 0.045
   eV/atom and energy RMSE < 0.060 eV/atom.

## What to submit

Produce a `manifest.json` at the submission root conforming to
`public/submission-schema.json`, declaring your artifacts (including the model
you used), provenance sources, lineage, and runtime receipts. Include your
reproduced `energy_mae_ev_per_atom` and `energy_rmse_ev_per_atom` in the
manifest `metrics` block with dataset `lc-mlp-v1-calibration-24`.

## Non-goals

- Retraining, fine-tuning, or regenerating the training data.
- Molecular dynamics, stability, or physical-observable validation.

The hard verifier outcomes are stated abstractly here; the hidden verifier
scores outcomes, not your directory layout or file names.
