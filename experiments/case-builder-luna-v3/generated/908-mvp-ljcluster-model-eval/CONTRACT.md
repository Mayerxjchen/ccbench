# Benchmark Case Contract — 908-mvp-ljcluster-model-eval

> Hidden evaluator-side contract (candidate_visible: false). Fields are filled
> from the validated reproduction spec; deferred states are declared, never
> defaulted.

- objective: evaluate the published LC-MLP v1 checkpoint on a
  candidate-generated evaluation descriptor table for LJ clusters and
  reproduce the paper's reported metric band (MAE <= 0.031 eV/atom, RMSE
  <= 0.045 eV/atom).
- source relationship (`paper_faithful` or `benchmark_adaptation`):
  paper_faithful.
- execution class: local_sandbox (no site fields, no HPC overlay assets).
- Runnable Draft admission basis: `check_discovery_runnable.py` (L1 gate)
  passing on this tree; state derives only from that checker's
  `--derive-state` record in `VALIDATION.json`.
- Discovery evidence and classifier output: none yet — the first real
  Discovery run follows `DISCOVERY-RUNBOOK.md` under the smoke profile after
  separate authorization.
- public/hidden boundary: candidate bundle = `public/**` + `instruction.md`
  only (positive allowlist; CONTRACT.md is NOT candidate-visible). Reference,
  solution, tests, thresholds, fixtures, evidence, and validation state are
  physically absent from the bundle.
- verifier outcome contract: `tests/test.sh` -> `tests/verifier.py` always
  writes `/logs/verifier/result.json` in the common result schema; plan
  layers MLP-V0/V1/V2/V4 + C-V7/C-V8 with MLP-V4 hidden science deferred at
  Discovery MVP.
- submission roles: candidate executes the published checkpoint and authors
  the metrics-bearing manifest; no training role exists for this kind.
- acceptance basis: published reference band MAE <= 0.031 eV/atom and RMSE
  <= 0.045 eV/atom (paper Table 1); formal frozen thresholds are deferred to
  threshold calibration (`reference/thresholds.json` status draft).
- Reference/Solution state (`planned`, `deferred`, or evidence-backed state):
  `reference/reference.json` state `planned`; `solution/expert/` deferred
  (README placeholder only); both are pre-PROMOTE and claim no executed
  evidence.
