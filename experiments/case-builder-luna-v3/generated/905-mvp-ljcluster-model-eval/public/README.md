# Case bundle — LC-MLP v1 model evaluation (Discovery MVP)

This bundle provides everything needed for the task, referenced by the paths
below. Paths are relative to the sealed submission root.

- `lc-mlp-v1/config.yaml` — published model and preprocessing configuration.
- `lc-mlp-v1/weights/lc-mlp-v1.txt` — the published LC-MLP v1 checkpoint.
- `lc-mlp-v1/paper.md` — the source paper (methods, descriptor definitions,
  reported metric band).
- `lc-mlp-v1/model-card.md` — a case-level model card distilled from the
  configuration and paper.
- `lj-clusters/descriptors.csv` — re-serialized 24-cluster descriptor table
  (`lj_cluster_calibration_24`).
- `lj-clusters/README.md` — column meanings and units.
- `system.json` — case system registry (interface `lj-cluster-eval`).
- `submission-schema.json` — the output manifest contract.
- `input-manifest.json` — inventory of the provided inputs above.

The set LJ-25..LJ-32 was held out by the original study and is not part of
the provided table. High-level task facts live in the instruction file at the
sealed root; the full contract (builders/harness view) is `CONTRACT.md`.