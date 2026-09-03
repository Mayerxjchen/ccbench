# Task Instruction — LC-MLP v1 cluster cohesive-energy model evaluation

- Source relationship: paper-faithful reproduction of a published model
  evaluation (`paper_faithful`). You evaluate the published LC-MLP v1
  checkpoint; you do not retrain it.
- Target systems and binding scientific constraints: Lennard-Jones clusters
  (`lj_clusters` in `public/system.json`), non-periodic, neutral; per-atom
  cohesive energy `energy_per_atom_eV` in eV/atom is the only scored
  observable. The descriptor basis uses a 6.0 angstrom distance cutoff with
  the five fixed descriptors defined in `public/descriptor-recipe.md`.
- Inputs that may and may not be used: you may use every file listed in
  `public/input-manifest.json` — the published calibration descriptor table
  `public/inputs/calibration-descriptor-table.csv` (LJ-01..LJ-24, with
  labels), the published model configuration
  `public/inputs/lc-mlp-v1-config.yaml`, and the published checkpoint
  `public/inputs/lc-mlp-v1-checkpoint.txt`. You may not use any held-out
  cluster data (LJ-25..LJ-32), any grader-side answer file, or any
  retrained/fine-tuned model in place of the published checkpoint.
- Scientific workflow that must be executed (preparing inputs alone is not
  completion): (1) re-serialize the evaluation descriptor table from the
  published calibration table in the schema of `public/descriptor-recipe.md`;
  (2) standardize the five features with the published train mean/std and
  execute the published checkpoint over the evaluation table; (3) compute
  energy MAE and RMSE in eV/atom against the table labels.
- Required reference-label coverage: every evaluation row carries its
  published `energy_per_atom_eV` label from the calibration table; no new
  labeling is required or permitted in this task.
- Training/iterative-improvement outcome and stopping criteria: none — this
  is a model-evaluation task; the checkpoint is fixed and immutable.
- Held-out grouping and numerical/physical validation outcomes: the
  evaluation set is the candidate-generated re-serialization of the
  calibration table (`calibration-table-lj01-lj24`); the author's held-out
  rows LJ-25..LJ-32 are hidden verifier material and are never distributed.
  Your submission must report energy MAE <= 0.031 eV/atom and energy RMSE
  <= 0.045 eV/atom on your evaluation table (the published reference band).
  A hidden verifier additionally rechecks submission integrity, artifact
  provenance, and model authenticity; hidden static accuracy scoring on the
  held-out set applies at release and is deferred during Discovery.
- Required machine-readable manifest, artifacts, failures, and provenance:
  deliver at the submission root a `manifest.json` conforming to
  `public/submission-schema.json` (case_id `908-mvp-ljcluster-model-eval`),
  declaring every artifact with its sha256, the evaluation lineage, runtime
  receipts for the executed evaluation, provenance sources, and your computed
  metrics (name, value, unit `eV/atom`, dataset
  `calibration-table-lj01-lj24`). Submissions that are empty, malformed,
  hash-inconsistent, or missing declared artifacts fail as agent failures;
  harness faults are classified separately as infrastructure invalid.
