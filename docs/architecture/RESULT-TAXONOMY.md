# Benchmark Result Taxonomy (v0)

**Status:** Normative. Owning classification and retry semantics.

## Top-level classes

```python
result_classes = {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}
```

### VALID_RESULT

Scientific evaluation completed and was decided:

- `PASS` — sealed submission satisfies the case's scientific verifier.
- `SCIENTIFIC_FAIL` — sealed submission fails the scientific verifier.

### AGENT_FAILURE

The Agent did not produce a scientifically decidable outcome:

- `AGENT_TIMEOUT`
- `AGENT_BUDGET_EXHAUSTED`
- `RESOURCE_EXCEEDED`
- `NO_SUBMISSION`
- `INVALID_SUBMISSION`
- `BAD_INPUT`

### INFRA_INVALID

The infrastructure, not the Agent, failed. Never counted as scientific failure:

- `SANDBOX_FAILURE`
- `HARNESS_FAILURE`
- `GATEWAY_FAILURE`
- `ADAPTER_FAILURE`
- `HPC_FAILURE`
- `VERIFIER_FAILURE`

## Counting rules

- `is_counted_scientifically == True` **only** for `VALID_RESULT`.
- `INFRA_INVALID` observations are **excluded from scientific success
  denominators** and reported as a separate infrastructure-invalid count.
- `AGENT_FAILURE` is a real, counted non-success — never relabeled as
  infrastructure.
- Exceptions are never collapsed into `reward = 0`. Every non-`VALID_RESULT`
  outcome carries an explicit `FailureCode` and machine-readable reason.

## Retry semantics

- `INFRA_INVALID` attempts **may** be retried with a new `run_id`; the
  replacement records its original attempt and the reason for retry.
- `AGENT_FAILURE` and `SCIENTIFIC_FAIL` **may not** be silently rerun; a new
  attempt is a new observation.
- A paper author freezes model, skill, case, resource and budget conditions
  before a comparison. Other outcomes remain independent observations and
  may not be silently rerun. The infra does not author the paper's experiment
  matrix or infer scientific validity from a signed receipt.
