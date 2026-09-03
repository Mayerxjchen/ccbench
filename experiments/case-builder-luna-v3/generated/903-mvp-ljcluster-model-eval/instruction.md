# Task — LC-MLP v1 cluster cohesive-energy evaluation

Source relationship: paper-faithful reproduction. The task evaluates the
published LC-MLP v1 checkpoint exactly as reported — **no retraining is
implied**; preparing inputs alone is not completion, the evaluation
workflow below must actually be executed and its artifacts submitted.

## Target system and binding scientific constraints

Argon Lennard-Jones clusters, compositions n in {8, 13, 19, 27, 38},
0 K static structures. The label quantity is cohesive energy per atom in
eV/atom. The canonical system scope is `public/system.json`; the fixed
evaluation data is the 24-row table `public/eval-table.csv` (rows
LJ-01..LJ-24) with reference energies included per row.

## Inputs you may and may not use

You may use only:

- `public/eval-table.csv` — the descriptor table (5 fixed features per row);
- `public/lc-mlp-v1.txt` — the published checkpoint to evaluate;
- `public/eval-config.yaml` — architecture (FFN, 6.0 angstrom cutoff, two
  hidden layers of width 32, silu), the feature order, and the recorded
  standardization constants (train_mean/train_std);
- `public/system.json` and `public/submission-schema.json`;
- your own inference and metric-computation code on the local sandbox.

You may not substitute a different model, refit or fine-tune weights, or
obtain reference labels from any source other than the provided table.
Held-out reference targets beyond the provided table are not distributed
to candidates.

## Scientific workflow that must be executed

1. Load the checkpoint `public/lc-mlp-v1.txt` and the config
   `public/eval-config.yaml`.
2. Standardize each feature of every row of `public/eval-table.csv` with
   the recorded train_mean/train_std constants.
3. Run the model's inference for every row to obtain a predicted cohesive
   energy per atom (eV/atom).
4. Compute `mae_ev_per_atom` and `rmse_ev_per_atom` of the predictions
   against the table's reference column, and record them in your
   submission metrics.

## Required reference-label coverage and metric band

Reference labels (cohesive energy per atom) are required for every row of
the provided table, and your submission must cover all 24 rows.
Draft metric band on the provided table — calibration is planned, this
MVP executes the technical chain: `mae_ev_per_atom` < 0.05 and
`rmse_ev_per_atom` < 0.075 (eV/atom).

## Training / iterative-improvement outcome

Not applicable: this is an evaluation-only task. There is no training or
model-improvement outcome to report.

## Held-out grouping and validation outcome

The verifier owns one additional author-retained group of 8 clusters
that continues the LJ id sequence beyond the provided table; it is not
distributed, never shown to you, and grouped by system (contiguous id
range) so results cannot be blended across groups. Scoring against that
group is a deferred release-stage scientific check; at this stage the
hard outcomes are the abstract chain below plus the draft metric band on
the provided table.

## Required machine-readable manifest, artifacts, and provenance

Seal a `manifest.json` at the submission root conforming to
`public/submission-schema.json` (schema_version 1; non-empty
`provenance_sources` naming every upstream source you consumed). Declare
at least these artifacts with their exact sha256 digests:

- `artifacts/model/lc-mlp-v1.evaluated.txt` — role `student_model`: a
  byte-identical copy of the checkpoint you evaluated;
- `artifacts/inputs/eval-table.copy.csv` — role `dataset`: a copy of the
  input table, with a non-empty `source` provenance string naming where
  it came from, and `frames` equal to the number of rows it holds;
- `artifacts/reports/predictions.csv` — role `report`: per-row predictions
  (row id, predicted eV/atom);
- `artifacts/reports/metrics.json` — role `report`: the computed
  `mae_ev_per_atom` and `rmse_ev_per_atom` with units.

The manifest must also carry:

- `lineage`: one entry per evaluation round with `round` values
  contiguous from 1, an `action` describing what was done, and
  `dataset` / `model` set to the declared artifact paths of the table
  copy and the evaluated checkpoint;
- `runtime_receipts`: at least one receipt of your real local execution
  with `job_id`, `backend`, and a success `exit_status`.

Absolute paths, parent traversal, symlinks, hardlinks, missing declared
artifacts, and hash mismatches fail the integrity check.
