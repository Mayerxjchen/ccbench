# Discovery Runbook — 903-mvp-ljcluster-model-eval

This case is a Runnable Draft (L1) produced by the Skill's
`check_discovery_runnable.py` gate. No Discovery run has happened yet;
this runbook is the first-real-run procedure. Nothing here has been
executed — all runtime statements below are planned.

## Prerequisites before the first real run

1. **Freeze the inference contract (blocker).** `blk-inference-serialization`
   in case-design.yaml is open: the toy checkpoint's serialization does
   not match the width-32 config architecture, so an executable
   inference contract must be pinned with the source owner (author
   clarification or a version-matched inference script) before any run
   whose outcome is treated as scientific. A smoke-class technical-chain
   run may proceed with that limitation stated in the evidence.
2. Re-run the L1 gate after any edit:
   `/Users/xjchen/bench/mlffbench/.venv/bin/python <skill>/scripts/common/check_discovery_runnable.py . --repo-root /Users/xjchen/bench/mlffbench --output MVP-READINESS.json --derive-state`
3. Confirm packaging works from the repo root: `CaseSpec` load +
   `package_candidate()` (the gate's bundle-agreement check already
   exercises this with the dftworld_bench package at
   `/Users/xjchen/bench/mlffbench`).

## First run: smoke profile (planned)

- Authorization: the operator who runs this must explicitly authorize
  local sandbox execution of candidate inference code; this build session
  was not authorized to run the model, MD, or any pipeline.
- Procedure (planned, not executed):
  1. Package the candidate bundle for a fresh agent run
     (`package_candidate()` against the repo-root case path).
  2. Drive a candidate agent with `instruction.md` + bundle only; it
     writes sealed `manifest.json` + artifacts per
     `public/submission-schema.json`.
  3. Run `bash tests/test.sh` with the sealed root mounted; read
     `/logs/verifier/result.json`.
  4. Expected smoke outcome: the technical chain (V0/V1/V2/V3/C-V7/C-V8)
     executes and MLP-V4's science is marked deferred — the same shape
     the fixture matrix encodes at `tests/fixtures/positive/structural-minimal/`.
- Evidence: store the run's result.json and submission under
  `evidence/` via the evidence manifest only after a run actually
  happens (retention policy: evidence/retention-policy.yaml).

## Classification follow-up

Any non-VALID_RESULT from a real run goes through the Skill's
`scripts/common/classify_failure.py` before touching the case, mapping to
INFRA_INVALID / INVALID_SUBMISSION / SCIENTIFIC_FAIL classes per
failure-taxonomy.yaml; do not hand-edit fixtures to make a run pass.

## Later stages (planned, not authorized in this build)

- Formal profile: after the blocker is frozen and thresholds move from
  `draft` toward `calibrating`/`frozen` (check_threshold_freeze.py),
  with the hidden held-out accuracy scoring implemented (reference.json
  leaves `planned`).
- L2: `check_release.py` is the only writer of benchmark_valid=true;
  nothing in this draft comes close to claiming it.
