# Task: evaluate the published LC-MLP v1 checkpoint on Lennard-Jones clusters

`requires_tools: python3` — `uname: Linux` (host header) — `gpu: false`

You are evaluating **LC-MLP v1**, a published fixed-feature feed-forward
network that predicts the per-atom cohesive energy of Lennard-Jones clusters
from five structural descriptors. No retraining is involved: you must run the
published checkpoint over a re-serialized descriptor table and report the
energy metric band.

All input paths below are relative to the sealed submission root and are
staged into it for you.

## Inputs

- `public/lc-mlp-v1/config.yaml` — published model and preprocessing
  configuration (architecture, input feature order, standardization mean/std).
- `public/lc-mlp-v1/weights/lc-mlp-v1.txt` — the published LC-MLP v1
  checkpoint.
- `public/lc-mlp-v1/paper.md` — methods and reported results (Table 1,
  metric band).
- `public/lc-mlp-v1/model-card.md` — distilled model card (no information
  beyond the configuration and paper).
- `public/lj-clusters/descriptors.csv` — re-serialized 24-cluster descriptor
  table `lj_cluster_calibration_24` (rows LJ-01..LJ-24).
- `public/lj-clusters/README.md` — column meanings and units.
- `public/system.json` — case system registry (interface `lj-cluster-eval`).
- `public/input-manifest.json` — inventory of the provided inputs.
- `public/submission-schema.json` — the output manifest contract.

## What to do

1. Load the published checkpoint and configuration from the inputs above.
2. For every row of `public/lj-clusters/descriptors.csv`, apply the recorded
   standardization to the five feature columns in order, run the network, and
   predict `energy_per_atom_eV`.
3. Compute aggregate metrics of your predictions against the
   `energy_per_atom_eV` label column of the descriptor table:
   - `mae_ev_atom` — mean absolute error in eV/atom;
   - `rmse_ev_atom` — root mean squared error in eV/atom.
4. Persist your per-cluster predictions (CSV) and the model identification,
   and write a `manifest.json` at the sealed submission root that conforms to
   `public/submission-schema.json`: reference the published checkpoint
   (role `student_model`), the prediction artifact (role `report`), the
   descriptor table (role `dataset`), plus provenance, lineage, runtime
   receipts and your `metrics`.

## Outcome contract

- A hidden verifier grades the technical chain: submission identity,
  provenance, model authenticity, lineage, runtime receipts, and
  filesystem/integrity safety. Report your metrics honestly in the `metrics`
  array with units `eV/atom`.
- The case's declared acceptance bounds are `mae_ev_atom` `< 0.10` eV/atom
  and `rmse_ev_atom` `< 0.10` eV/atom. Hidden static-accuracy verification of
  these bounds is deferred release work at the Discovery stage; your run is a
  Discovery diagnostic, not a benchmark scientific verdict.