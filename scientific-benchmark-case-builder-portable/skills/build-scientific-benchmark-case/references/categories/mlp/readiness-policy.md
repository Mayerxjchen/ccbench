# Reproducibility Readiness Policy

> Load this when writing `reproducibility-assessment.yaml` or deciding whether extraction can stop.

Readiness is a deterministic **information sufficiency** assessment. It is not a scientific-quality verdict.

## Dimensions

Always assess independently:

```yaml
readiness:
  extraction_completeness:
  execution_readiness:
  verification_readiness:
  benchmark_case_readiness:
```

Canonical statuses:

```text
ready
recoverable
blocked
not_applicable
```

Definitions:

- `ready`: the information/artifacts required for that dimension and requested scope are present without unresolved critical conflicts.
- `recoverable`: specific missing information appears obtainable from known artifacts, configuration recovery, or bounded follow-up; work is not executable/verifiable yet.
- `blocked`: a critical artifact/value is unavailable, restricted without access, irreconcilably conflicting, or not recoverable from the known evidence.
- `not_applicable`: the dimension does not apply to the requested scope.

## Never conflate readiness with scientific adequacy

This skill may say:

```yaml
execution_readiness: ready
```

meaning the documented procedure appears executable.

It must not say:

```yaml
production_readiness: ready
```

Production readiness belongs to scientific MLIP review.

Always output:

```yaml
production_readiness:
  status: not_assessed
  owner: mlp
```

## Criticality is scope-dependent

The same missing field can be optional in one scope and blocking in another.

### Final-model retraining

Normally required for `execution_readiness: ready`:

- primary target model identity/role;
- training dataset identity and usable artifact/path/manifest;
- implementation/framework identity sufficient to invoke the target trainer;
- model-family/architecture information required by the trainer;
- essential training controls required to reproduce the reported recipe;
- no unresolved critical conflict in the above.

Usually nonblocking by itself:

- active-learning exploration thresholds when the final labeled dataset is already available;
- random seed when the paper does not claim bitwise-identical weights and training remains executable;
- downstream property-analysis details not needed for model-level verification.

Normally required for `verification_readiness: ready`:

- a model-level validation dataset/split or an equivalent released verifier fixture;
- metric definition(s) and units;
- reference target(s) or a clear comparison procedure;
- no unresolved critical conflict in verification identity.

### Full data generation and training

Add as execution-critical:

- initial structure/source policy;
- reference-label method fingerprint sufficient for new labels;
- sampling/exploration conditions;
- active-learning or selection logic when used;
- stopping/convergence criteria;
- data assembly/deduplication policy when material to the final dataset.

### Published model execution

Execution-critical:

- trained model artifact identity;
- compatible implementation/runtime;
- model input/type mapping and inference entrypoint;
- required normalization/metadata files.

Training hyperparameters can be non-applicable.

### Model evaluation only

Verification-critical:

- exact model artifact or reproducibly trained target;
- test dataset/split identity;
- metric implementation/definition and units;
- any preprocessing needed to match the reported metric.

### Training recipe recovery

`execution_readiness` may be `recoverable` or `blocked` while `extraction_completeness` is `ready` if the recipe has been faithfully recovered but required artifacts are unavailable.

### Reproducibility screening

The goal is classification, not execution. `extraction_completeness` can be `ready` when enough evidence exists to classify execution/verification as ready/recoverable/blocked.

## Deterministic blocker rules

A readiness dimension cannot be `ready` when any required field for that dimension is:

- missing;
- `claim_status: unknown`;
- `claim_status: conflicting`;
- backed only by an unresolved critical conflict;
- dependent on an artifact whose access status is `unavailable`;
- dependent on a restricted artifact for which no access path exists.

Use `recoverable` instead of `blocked` only when the assessment names a plausible bounded recovery route, such as a known SI, repository tag, dataset record, or config artifact not yet inspected.

## Conflict handling

Any unresolved `critical` conflict touching execution identity blocks `execution_readiness: ready`.

Any unresolved `critical` conflict touching verifier identity blocks `verification_readiness: ready`.

Do not resolve conflicts inside the readiness calculation.

## License handling

Unknown or restrictive licenses do not automatically block private/internal reproduction.

For `benchmark_case_readiness`, however:

- unknown redistribution rights -> at best `recoverable`;
- known prohibition on necessary redistribution -> `blocked` for a self-contained public benchmark unless the benchmark can reference rather than redistribute the asset;
- explicit compatible license + executable/verification readiness -> may be `ready` from an information/packaging standpoint.

This still does not make the benchmark scientifically valid.

## Recommended assessment shape

```yaml
schema_version: 1
scope: final_model_retraining

missing:
  - field: training.batch_size
    critical_for: []
    recovery: inspect version-matched training config

conflicts: []

blockers: []

readiness:
  extraction_completeness: ready
  execution_readiness: recoverable
  verification_readiness: blocked
  benchmark_case_readiness: blocked

production_readiness:
  status: not_assessed
  owner: mlp
```

## Stop rule

Extraction may stop when:

- the requested scope is `ready` for both execution and verification; or
- remaining gaps are explicitly classified as recoverable/blocked and further reading of currently available sources is unlikely to change the classification.

Do not continue into unrelated scientific sections solely to maximize filled fields.

## Runnable Draft readiness

For `full_data_generation_and_training`, Runnable Draft readiness additionally
requires the MLP Prompt slots in `prompt-contract.md` and a passing
`check_draft_consistency.py` result. Expert model quality and production
readiness remain unassessed; they do not become implied by Draft readiness.
