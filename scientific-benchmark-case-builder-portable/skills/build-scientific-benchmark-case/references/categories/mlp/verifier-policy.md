# MLP Verifier Policy

Selectable verifier layers for MLP cases. The category proposes which layers
apply for a case kind; Common Core requires that every proposed hard outcome
map to evidence, a check, a failure fixture, and an acceptance basis.

Common layers (resource/provenance compliance, submission integrity) are owned
by Common Core and are appended to the MLP layers for every case.

## MLP verifier layers

```text
MLP-V0 system/submission identity
MLP-V1 data/label provenance
MLP-V2 model authenticity
MLP-V3 iterative workflow integrity (conditional)
MLP-V4 hidden static accuracy
MLP-V5 hidden dynamic stability (conditional)
MLP-V6 hidden physical observable (conditional)
```

## Applicability by case kind

| Case kind | Mandatory | Conditional |
|---|---|---|
| `final_model_retraining` | V0, V1, V2, V4 | V3, V5, V6 |
| `end_to_end_model_development` | V0, V1, V2, V4 | V3, V5, V6 |
| `active_learning_workflow` | V0, V1, V2, V3, V4 | V5, V6 |
| `published_model_execution` | V0, V2, V4, V6 | V1, V3, V5 |
| `model_evaluation` | V0, V2, V4 | V1, V3, V5, V6 |

## Rules

- Exact expert directory names and scripts are never required for scoring.
- An alternative-valid fixture must prove outcome-based scoring.
- A negative fixture is required for every hard (non-conditional) outcome.

## Threshold freeze

Every MLP hard outcome threshold freezes per
`references/common/threshold-calibration.md`. Hidden-set independence
(generation provenance, Candidate inaccessibility, submission-overlap check)
is mandatory for a frozen MLP threshold. Static accuracy bounds and dynamic
stability bounds are stochastic metrics: a frozen `stochastic: true` threshold
requires `n_runs >= 2` from independent reference runs.
