# Discovery Runbook — 906-mvp-ljcluster-model-eval

This case is a `runnable_draft` once `check_discovery_runnable.py` passes with
`--derive-state`. It is **not** `benchmark_valid`. No real Discovery run has
been executed yet.

## First real Discovery run (authorized users only)

The first real attempt uses the **smoke-class** Discovery profile, never
`formal`. Do not submit HPC jobs, build containers, train a model, or run MD.

1. Confirm the runtime/site is separately authorized. Runnable files being
   complete is **not** permission to run; runtime authorization is outside L1.
2. Package the Candidate bundle with the real packager:
   `dftworld_bench.core.packager.package_candidate(CaseSpec.load(CASE), dest)`.
3. Launch the Agent against the packaged bundle under the smoke profile
   (`profiles/smoke.yaml`), `local_sandbox` class.
4. Seal the submission, then run the hidden verifier mount:
   `SUBMISSION_ROOT=<sealed> RESULT_DIR=<logs/verifier> BENCH_RUN_ID=<id> bash tests/test.sh`.
5. Record the run durably (source commit, bundle digest, runtime identity, run
   and job IDs, logs, retained artifacts, failure code).
6. Classify the run with `scripts/common/classify_failure.py` and
   `references/common/failure-taxonomy.yaml`; record REJECT / REFINE / PROMOTE.

## Deferred release work (post-PROMOTE)

- MLP-V4/V5/V6 hidden scientific verification.
- Expert reference run and independently reproduced lineage.
- Double-threshold calibration freeze (independent evidence, before formal
  Agent results are inspected).
- Scientific positive/alternative-valid fixture closure.
- G0–G12 release gate closure and evidence retention.

## Honesty

`benchmark_valid=false`. Reference is `planned`; thresholds are `draft`. Nothing
in this case claims completed reference, calibration, or hidden science.
