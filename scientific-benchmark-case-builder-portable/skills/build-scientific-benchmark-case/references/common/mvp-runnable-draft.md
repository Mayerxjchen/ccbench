# Discovery MVP and `runnable_draft` (authoritative)

This file is the single authoritative definition of `runnable_draft` and the
L1 Discovery MVP boundary. Every other reference, script message, and template
that mentions the state links here instead of restating criteria.

## Three-layer funnel

| Layer | Goal | Declaring state |
|---|---|---|
| L0 Scaffold | files and sources parse into the case contract | `draft` |
| L1 Discovery MVP | Candidate packages and launches; submission verifiable; results standard; failures attributable | `runnable_draft` |
| L2 Release | reference, hidden science, thresholds, fixtures, evidence closure | `benchmark_valid` |

This round of optimization ships L1 only. L2 rules are unchanged and are never
weakened by an MVP pass.

## What `runnable_draft` means

A case is `runnable_draft` exactly when
`scripts/common/check_discovery_runnable.py CASE` exits 0. That single gate
executes, with the repository/runtime interpreter:

1. `validate_case.py` — structural Common Core contract;
2. category semantic validation and readiness (MLP: `validate_spec.py`,
   `check_readiness.py`; recoverable is allowed, blocked is not);
3. `derive_verifier_plan.py`, compared layer-by-layer against the case plan:
   every applicable layer must appear explicitly with `status: selected` or
   `status: deferred`, and a mandatory layer may only be deferred with a
   non-empty reason — applicable layers never silently disappear (a case kind
   that mandates V4 must ship V4 or explain its deferral);
4. `generate_fixture_matrix.py` — plus the executable negative fixtures
   (`empty`, `forged-manifest`, `missing-model`, `broken-lineage`, and the
   four integrity probes `missing-artifact`, `multi-hash-mismatch`,
   `missing-plus-mismatch`, `type-garbage-manifest`) and one structural
   positive fixture actually present under `tests/fixtures/`;
5. `check_draft_consistency.py` — submission root, instruction/bundle paths,
   input-manifest candidate paths, held-out ownership, metric comparators,
   capability-vs-label-source, CONTRACT.md visibility, and leave-one-out
   source context (invariant H: `source_context.allow` roots versus
   `source/sources.lock.json`, with answer names like `acceptance.json`
   never consumed);
6. the **actual** `CaseSpec.load` + `package_candidate()` from the repository
   into a temporary directory;
7. bundle agreement — instruction, input manifest, submission schema, and
   CONTRACT.md checked against the **real** packaged bundle;
8. verifier mount smoke — `tests/test.sh` run in a faithful `/tests` +
   sealed-root + result-directory layout: empty submission and forged/broken
   submissions must produce `AGENT_FAILURE`, the structural fixture must
   produce `VALID_RESULT` whose reason marks hidden science deferred, and a
   broken entry must produce `INFRA_INVALID`. The four integrity probes are
   graded by exact attribution and retryability, not merely run:
   a missing declared artifact must be named as missing, *every* corrupt hash
   must be named in one result (no short-circuiting behind an earlier
   finding), and a type-invalid manifest must classify as
   `INVALID_SUBMISSION` with `retryable: false` — never escape as an
   infrastructure crash the harness would excuse;
9. every produced `result.json` validates against the common result schema
   with failure attribution in `reason`;
10. honesty — `benchmark_valid=false`, and reference/thresholds may be
    `planned`/`deferred` but must not claim completed states without evidence.

## Who may write the state

- `scaffold` (init_case) only ever produces `draft`.
- Only `check_discovery_runnable.py --derive-state` writes
  `case_status: runnable_draft` into `VALIDATION.json`, together with its
  `runnable_derivation` record. `validate_case.py` rejects the state without
  that record.
- Any validator that is missing a dependency, was not run, or returned
  non-zero blocks the state. A YAML/Ruby syntax parse is never a substitute
  for a semantic validator; the checker runs every semantic validator as a
  subprocess and records the interpreter.
- The runtime/site authorization is outside L1: a case may reach
  `runnable_draft` while real Discovery execution remains blocked on site
  qualification. Runnable files being complete ≠ permission to run.

## What `runnable_draft` does NOT mean

It is not verifier completion, not scientific acceptance, and not release.
Specifically it does not claim:

- an expert reference or alternative implementation;
- frozen dual thresholds;
- MLP-V4/V5/V6 hidden scientific verification (these are reported as
  `deferred_release_work`);
- G0–G12 closure, evidence retention closure, or `benchmark_valid=true`;
- an `experiment-handoff` or ablation packet.

## First real run

The first real attempt uses the `discovery` path: run the launch command in
`DISCOVERY-RUNBOOK.md` under the smoke-class profile and separately authorized
runtime, never the formal profile. Record the run, then classify with
`scripts/common/classify_failure.py` and
`references/common/failure-taxonomy.yaml` (see `discovery-and-refinement.md`).
Reference, hidden static accuracy, and thresholds may be explicitly
`planned`/`deferred` at this stage.

## Command-level `mvp` mode

`/build-scientific-benchmark-case mode=mvp category=mlp` composes
intake → extract-spec → design → scaffold → minimal construct → this gate in
one pass, pausing at materially scientific target choices, unclear
download/license states, container builds, real scheduler submissions,
expensive training/DFT/MD, and destructive overwrites. Default outputs: the
case tree, `MVP-READINESS.json` (the checker report), and
`DISCOVERY-RUNBOOK.md`. It never emits ablation or release packets.
