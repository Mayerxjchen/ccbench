# Threshold Calibration

Thresholds freeze only from independent calibration evidence collected before
formal Agent results are inspected. `check_threshold_freeze.py` is the only
writer authority for a frozen state; Claude never hand-frozens thresholds.

## States

```text
draft -> calibrating -> frozen
```

- `draft`: no calibration evidence yet.
- `calibrating`: reference runs in progress; freeze record incomplete.
- `frozen`: full freeze record present and independently validated.

## Freeze record

A frozen threshold carries, per metric key:

- `pass_bound`, `stochastic`, `statistics{mean,std,n_runs}`
- `reference_run_ids` and `reference_digests` (run identity + digest)
- `units` and `metric_definitions` covering every threshold key
- `rationale`, `calibrated_at`, `case_version`, `verifier_version`
- `produced_by: threshold_calibration`
- `agent_results_inspected_at: null` (freeze must precede Agent inspection)
- `hidden_set`: generation provenance, candidate inaccessibility, and
  submission-overlap check

## Fail-closed rules

- One-run stochastic calibration is rejected: `stochastic: true` requires
  `n_runs >= 2`.
- A freeze timestamp after Agent results were inspected is rejected.
- A threshold written by an expert runner (`produced_by !=
  threshold_calibration`) is rejected.
- Missing units or metric definitions for any threshold key is rejected.
- An expert value copied directly as a tight pass bound is rejected: the pass
  bound must not equal the reference metric value.

## Hidden-set independence

The hidden set records generation provenance, Candidate inaccessibility, and a
submission-overlap check. Expert mother datasets may seed hidden data only when
the access and overlap arguments are independently testable; those arguments
are part of the freeze record, not an assumption.
