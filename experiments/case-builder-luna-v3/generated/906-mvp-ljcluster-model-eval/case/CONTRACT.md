# Benchmark Case Contract

- objective: Evaluate the published LC-MLP v1 checkpoint on the 24-cluster
  Lennard-Jones descriptor table and reproduce the reported per-atom
  cohesive-energy metric band (paper Table 1: MAE 0.031, RMSE 0.045 eV/atom).
- source relationship: `paper_faithful`
- execution class: `local_sandbox`
- Runnable Draft admission basis: `scripts/common/check_discovery_runnable.py`
  exit 0 (structural + semantic + real packaging + bundle agreement + verifier
  mount smoke + honesty).
- Discovery evidence and classifier output: pending — no real Discovery run has
  been executed; see `DISCOVERY-RUNBOOK.md`.
- public/hidden boundary: Candidate sees the positive-allowlist bundle
  (`public/`, `instruction.md`, `task.toml`, `CONTRACT.md`). Hidden reference,
  solution, tests, thresholds, and fixtures are physically absent from the
  bundle.
- verifier outcome contract: `tests/test.sh` -> `tests/verifier.py` always emits
  the common `result.json` (VALID_RESULT / AGENT_FAILURE / INFRA_INVALID).
- submission roles: `student_model` (the evaluated checkpoint), plus declared
  provenance/lineage/receipts in `manifest.json`.
- acceptance basis: energy MAE < 0.045 eV/atom and energy RMSE < 0.060 eV/atom
  on the 24-cluster calibration table (candidate-visible band; hidden V4 static
  accuracy is deferred to release work).
- Reference/Solution state: `planned` / `deferred` — no expert reference run,
  no frozen thresholds, no hidden V4/V5/V6 science at this MVP stage.
