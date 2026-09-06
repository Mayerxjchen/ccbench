# Discovery Runbook — 908-mvp-ljcluster-model-eval

First real Discovery run for this Runnable Draft. Follow it only after the L1
gate (`check_discovery_runnable.py`) has passed and `--derive-state` has
recorded `runnable_draft` in `VALIDATION.json`.

## Preconditions

- `MVP-READINESS.json` reports `mvp_runnable: true` (this tree).
- Execution is separately authorized: running the Candidate and Verifier is a
  real execution step and is not implied by the draft state.
- Runtime: local sandbox only (`execution.class: local_sandbox`); a python3
  interpreter with the standard library suffices for the smoke path. No
  network, no container build, no HPC submission.

## Launch (smoke profile)

1. Package the Candidate bundle with the real packager
   (`ccbench.core.packager.package_candidate`) from this case root.
2. Materialize a Candidate sandbox containing the bundle
   (`instruction.md` + `public/**`) and nothing else.
3. Run the Agent against `instruction.md` under `profiles/smoke.yaml`
   (plumbing verification only — never the formal profile for a first run).
4. Destroy the Candidate; mount a fresh hidden Verifier with the sealed
   submission: `SUBMISSION_ROOT=<sealed> RESULT_DIR=<logs/verifier>
   BENCH_RUN_ID=<id> bash tests/test.sh`.
5. Record the durable run record (bundle digest, submission digest,
   result.json, reward).

## After the run

Classify the attempt with `scripts/common/classify_failure.py` against
`references/common/failure-taxonomy.yaml` (source / infrastructure /
case-design / runtime / resource / Agent) before any REJECT / REFINE /
PROMOTE decision. A failed run is evidence; do not patch the case silently.

## Deferred release work (not part of Discovery)

- expert reference run and independently reproduced lineage
  (`reference/reference.json` is `planned`);
- MLP-V4 hidden static accuracy on the author-held-out rows LJ-25..LJ-32
  (hidden set generation + gold labels require authorization);
- dual-threshold calibration freeze (`reference/thresholds.json` is `draft`);
- scientific positive / alternative-valid fixture closure beyond the
  structural fixtures;
- G0–G12 release gate closure and evidence retention.
