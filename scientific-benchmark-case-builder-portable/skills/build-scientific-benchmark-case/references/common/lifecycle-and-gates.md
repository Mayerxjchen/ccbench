# Lifecycle and Gates

Common release gate groups are category-neutral. A category adds sub-gates
inside a group; it never adds new top-level lifecycle semantics.

## Quality funnel

```text
draft -> runnable_draft -> discovery_complete
  -> rejected | needs_refine | promoted
  -> reference_validated -> verifier_validated
  -> benchmark_valid -> experiment_ready
```

Runnable Draft admission and Discovery classification precede the release gate
groups below. They screen case value and attribute failures before expensive
expert work; they never imply `benchmark_valid=true`.

## Gate groups

```text
G0 source/category/scope freeze
G1 Candidate public/hidden boundary
G2 runtime and execution contract
G3 instruction/output-contract fidelity
G4 expert reference reproducibility
G5 hidden validation independence
G6 outcome-based verifier coverage
G7 positive/negative/alternative-valid fixtures
G8 threshold calibration
G9 evidence/provenance integrity
G10 end-to-end reward chain
G11 independent rerun/reproducibility
G12 final release freeze
```

## Writer authority

- The Builder may write `draft`, `constructed`, and component-validation states.
- Only deterministic release derivation (`check_release.py`, Task 9) may write
  `benchmark_valid=true`.
- `benchmark_valid.json` is generated from sealed evidence and hashes; it cannot
  be asserted by Claude or copied from a template.
- A case with any required gate open is invalid by construction (fail-closed).

## Authorization boundaries

Construction pauses before expensive execution, destructive cleanup, or choosing
among materially different scientific validation targets. Execution approval is
per step, never standing.
