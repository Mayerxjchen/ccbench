# Discovery Runbook — LC-MLP v1 model evaluation

Builders/harness view. Describes the first real Discovery run and what is
deferred. State and maturity are derived by the L1 gate only — this file
never asserts `runnable_draft` or `benchmark_valid`.

## Maturity funnel for this case

- Current state: `draft` (VALIDATION.json `case_status: draft`, all gates
  open, `benchmark_valid: false`).
- Derived state: only `scripts/common/check_discovery_runnable.py
  --derive-state` writes the `runnable_derivation` record that moves the case
  to `runnable_draft`. If the gate records the record, the case becomes
  admissible for its first real Discovery run.
- Later states (deferred, not reachable in this pass): discovery_complete,
  promoted, reference_validated, verifier_validated, benchmark_valid
  (never asserted here), experiment_ready.

## Preparation status (what this build established)

- Source intake and lock: leave-one-out intake holds — `source_context`
  declared in `case-design.yaml`, `source/sources.lock.json` records the
  audited exclusion patterns, none of the grader-artifact classes were locked
  or consumed.
- Reproduction spec: `source/mlp-reproduction-spec.yaml` (scope
  `model_evaluation_only`) + `source/source-evidence-map.yaml`, validated by
  `validate_spec.py`; readiness in
  `source/reproducibility-assessment.yaml` (no blocked dimension).
- Verifier plan: `verifier-plan.yaml` derived (schema 2), layers V0, V1, V2,
  V4, C-V7, C-V8 all `selected`, none deferred.
- Verifier mount: `tests/test.sh` + `tests/verifier.py` + fixture matrix
  (8 negatives, structural-minimal positive, alternative-valid placeholder)
  staged from the case template.

## Launching the first real Discovery run (separately authorized)

1. Package the candidate bundle with the repository packager
   (`CaseSpec.load` + `package_candidate`) into a sealed submission root;
   confirm the staged layout with `bundle_agreement`.
2. Run the **smoke-class profile** (`profiles/smoke.yaml`, `local_sandbox`)
   with the sealed verifier mounted: `/tests` (test.sh + verifier.py +
   fixtures), the sealed root, and `/logs/verifier/result.json` as the
   result dir.
3. Record the run record as durable evidence: source commit, bundle digest,
   runtime/site identity, run/job ids, logs, artifacts, and the emitted
   `result.json` (must validate against the common result schema).
4. Classify with `scripts/common/classify_failure.py` using
   `references/common/failure-taxonomy.yaml`:
   SOURCE_BLOCKED → REJECT; INFRA/CASE_DESIGN/RUNTIME/RESOURCE_BLOCKED →
   REFINE; otherwise → PROMOTE.
5. Always re-verify the sealed root before any release action.

## Deferred release work (honestly unexecuted)

- Expert reference evaluation run and independent parser.
- Assembly and freeze of the hidden held-out set (LJ-25..LJ-32) and
  double-threshold calibration of `mae_ev_atom` / `rmse_ev_atom`.
- Verifier closure for MLP-V4 (hidden static accuracy) and evidence
  retention sealing.
- G0..G12 gate closure and any `benchmark_valid` derivation (this pass makes
  no such claim).