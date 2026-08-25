# Reference and Expert Solution Policy

The reference anchors threshold calibration; the expert solution is one valid
implementation the verifier must accept. Both are hidden from the Candidate.

## Reference states

```text
planned -> inputs_frozen -> smoke_executed -> formal_executed
  -> independently_verified -> reproducible
```

- `planned`: design-only; nothing executed, inputs not hashed.
- `inputs_frozen`: every reference input is hashed; no further edits.
- `smoke_executed`: the smoke profile ran end-to-end on frozen inputs.
- `formal_executed`: the formal profile ran end-to-end on frozen inputs.
- `independently_verified`: a parser not authored by the expert that produced
  the outputs confirmed the metrics used for calibration.
- `reproducible`: rerun from frozen inputs under the recorded runtime identity
  reproduces the recorded metrics within tolerance.

State regression requires a recorded reason.

## Runnable provenance

Archived outputs without executable inputs, runtime identity, commands, and an
independent parser cannot become a reproducible reference. `reference.json`
records `state`, `raw_evidence` (each path hashed), `metrics`, `lineage`, and
`independent_parser`. A lineage entry is `{capability, inputs, outputs,
commands, runtime_identity, digest}`; empty lineage is only valid at `planned`.

## Approval-bound execution

The Skill may construct plans, scripts, and preflight checks. It pauses before
downloads with unclear rights, container builds, expensive calculation, remote
mutation, or scheduler submission unless that execution step is already
authorized.

## Solution policy

- `solution/expert` holds one valid reference implementation.
- An alternative valid implementation must also be accepted by the verifier;
  the instruction states the outcome abstractly and never discloses the expert
  method when alternatives exist.
- Expert outputs feed threshold calibration; the independent parser is a
  separate trust domain from the expert that produced the outputs.

## Pre-PROMOTE and fail-closed states

Before PROMOTE, Reference and Solution may be `planned` or `deferred`. Partial
assets must not claim `completed`, invent model paths, rounds, labels, metrics,
or validation evidence. A missing required artifact is a blocker, never a
default value.

After PROMOTE, preflight imports and exercises production code rather than a
copied approximation. Production and preflight share path resolution, method
fingerprint, units, and scheduler protocol. A submitted job is usable only
after a scheduler terminal state; fixed sleeps are not completion checks.
