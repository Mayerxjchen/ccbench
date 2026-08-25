# Discovery and Refinement

Load this for Runnable Draft admission, real Discovery runs, failure
classification, and REJECT/REFINE/PROMOTE decisions.

## Runnable Draft admission

A Runnable Draft has a stable scientific target, evidence-backed public
specification, leak-free Candidate bundle, resolved execution/runtime contract,
and durable run provenance. It remains `benchmark_valid=false`.

Expert reference, frozen thresholds, formal hidden-Verifier closure, and
independent reruns are post-PROMOTE investments. Their absence does not block
Discovery when they are explicitly `planned` or `deferred`.

## Discovery evidence

Record source commit, Candidate bundle digest, runtime/site identity, run and
job IDs, logs, retained artifacts, failure code, and classifier rationale. Run
the real declared execution path; a script-only or mocked result is not
Discovery evidence.

Classify with `scripts/common/classify_failure.py` and
`references/common/failure-taxonomy.yaml`. A failed run is evidence, not an
automatic Agent grade:

- `SOURCE_BLOCKED` -> REJECT or explicitly rescope;
- `INFRA_INVALID`, `CASE_DESIGN_BLOCKED`, `RUNTIME_BLOCKED`, or
  `RESOURCE_BLOCKED` -> REFINE and repeat Discovery;
- no blocker, or only `AGENT_LIMITATION` on a sound case -> PROMOTE.

Do not spend expert-reference cost before the decision is recorded.
