# Common Case Standard

The Common Core contract every benchmark case directory satisfies, independent
of scientific category. Category modules add fields; they never weaken these
invariants.

## Maturity states

```text
draft
  -> runnable_draft
  -> discovery_complete
  -> promoted
  -> reference_validated
  -> verifier_validated
  -> benchmark_valid
  -> experiment_ready
```

- `draft`: scaffolded structure; design and blockers recorded, nothing executed.
- `runnable_draft`: public science/runtime contracts are coherent and a real
  Discovery run can be attributed; expert/reference assets may be deferred.
- `discovery_complete`: one real run and its failure classification are durable.
- `promoted`: Discovery justifies post-PROMOTE reference investment.
- `reference_validated`: expert reference ran and an independent parser
  confirmed the metrics used for calibration.
- `verifier_validated`: hidden verifier and fixtures pass on reference and
  alternative-valid fixtures, and reject negatives.
- `benchmark_valid`: every required gate is closed with sealed, restorable
  evidence. Only deterministic release derivation writes this state.
- `experiment_ready`: a valid case frozen for pilot/formal experiments.

State regression is not allowed without a recorded reason; the release
derivation is the only writer of `benchmark_valid`.

## Directory contract

See the Task 4 scaffold manifest. Every case contains:

```text
CONTRACT.md
instruction.md
task.toml
case-design.yaml
public/
source/
reference/
solution/expert/
tests/fixtures/{positive,negative,alternative-valid}/
profiles/{smoke,formal}/
tools/
evidence/{retention-policy.yaml,manifest.json}
evaluator-manifest.json
VALIDATION.json
benchmark_valid.json
```

`benchmark_valid.json` is generated, never hand-authored. Its template value is
always `false`.

## Non-negotiable invariants

- Candidate-visible content is built from a positive allowlist. Hidden
  reference, solution, tests, thresholds, fixtures, old runs, Git metadata,
  host state, and credentials are physically absent from the Candidate bundle.
- Candidate and Verifier are separate trust domains. Local compute is sandboxed;
  the Candidate is destroyed before a fresh hidden Verifier runs.
- Thresholds freeze only from independent calibration evidence collected before
  formal Agent results are inspected.
- No downloads, training, labeling, MD, container builds, remote mutations, or
  HPC submission without explicit authorization for that execution step.
- Experiment handoff is unavailable until the case is independently valid.
