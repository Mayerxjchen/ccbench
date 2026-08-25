# MatClaw 032–033 Benchmark Finalization Design

## Purpose

Finalize Cases 032 and 033 as scientific agent-evaluation tasks. The evaluated
agent is not required to reproduce the literature result. An agent failure is a
valid benchmark outcome and may be retained as a negative diagnostic example.

The benchmark constructor must nevertheless demonstrate that each released
task is solvable. A hidden reference implementation may follow the documented
literature workflow and must pass the hidden verifier at least once before the
case is released. This constructor-side solvability run is distinct from an
evaluated agent run.

This design supersedes any 032/033 requirement in the earlier strict
construction design that treats two successful evaluated-agent reproductions
as a prerequisite for benchmark construction. It does not change Case 031.

## Completion States

Track independent facts rather than one overloaded validity flag:

- `construction_valid`: task layout, inputs, provenance, isolation, and
  verifier contracts pass.
- `smoke_validated`: the environment and reduced workflow execute correctly.
- `oracle_calibrated`: a hidden literature-informed reference solution passes
  the paper-profile verifier at least once.
- `agent_passed`: the particular evaluated-agent submission passes. This value
  may be false for a valid released benchmark.
- `benchmark_valid`: true only when construction, smoke, and oracle calibration
  are true. It does not depend on `agent_passed`.

Missing or corrupt evidence remains fail-closed for the fact that evidence is
supposed to establish.

## Public and Hidden Boundaries

Public task inputs contain only information that a participant is entitled to
use. Literature-derived expected values, future decision points, reference
scripts, acceptance tolerances, and golden artifacts remain in hidden
`solution/`, `reference/`, or verifier-owned data.

The hidden reference solution may directly encode a literature-supported
workflow. Its role is to prove solvability and calibrate the verifier, not to
model how an evaluated agent must reason. At least one alternative or synthetic
fixture must exercise the verifier independently of the primary golden result.

## Case 033: Truly Adaptive Search

### Public contract

The public profile exposes only the starting condition, physical bounds,
per-round job cap, total round cap, deterministic seed contract, MD fidelity,
and policy parameters. It must not expose the fixed 14-job literature path or
the best literature region.

### Search policy

`propose_round` is a deterministic function of the immutable configuration and
the complete preceding measured history. It selects no more than two new points
per round. Proposals use measured slope, flip count, sequentiality, and validity
flags to refine field and temperature; they may not index a pre-recorded future
path. Replaying the same seed and measured history must reproduce the same next
proposal.

The hidden reference solution may use literature knowledge to choose policy
parameters and tie-breaking rules, but the public evaluated workflow must still
derive each next point from prior measurements.

### Verification

The verifier independently reconstructs the round dependency chain from raw
trajectories and history. It rejects future-point leakage, duplicated or
out-of-bounds proposals, more than two jobs per round, missing raw evidence,
tampered derived metrics, and proposals made before their dependencies exist.

A passing scientific result requires sequential propagation, slope greater
than `0.3 ps/site`, and a best field in the inclusive interval
`[-0.18, -0.14] V/angstrom`. Temperature and any additional tolerances remain
locked in hidden acceptance data.

Required negative fixtures are:

1. a preset literature-path submission;
2. a valid-looking result whose best field is outside the acceptance interval;
3. a submission with a missing, reordered, or forged temporal dependency
   chain.

The existing `-0.13 V/angstrom` diagnostic is an evaluated-agent failure, not
evidence that the benchmark is invalid.

## Case 032: Restartable Temperature Runner

Each production temperature is an independent, atomic work unit with locked
inputs, seed, expected atom count, expected frame count, and completion
manifest. A later process invocation skips only temperature units whose raw
artifacts and manifest hashes pass validation.

The partial 350 K trajectory with 1499 of 2501 frames is diagnostic evidence
only. It must not be appended to unless a complete engine checkpoint proves
that positions, velocities, thermostat/integrator state, step number, and RNG
state are all restorable. Under the current evidence, 350 K is rerun from the
locked initial state and seed. Temperatures 375–600 K run as new independent
units.

Temporary outputs are written under a run-scoped path and promoted atomically
only after exact frame, atom, finiteness, metadata, and hash checks pass. A
failed unit remains rerunnable without invalidating completed temperature
units.

The hidden literature-informed solution must produce one complete paper-profile
dataset that passes the independent Tc analysis and the locked literature
tolerance. An evaluated agent may still time out, stop early, or produce an
incorrect Tc and receive a failing score.

## Isolation and Scope

- Do not modify Case 031 or its Task 9 execution files.
- Do not promote existing diagnostic outputs into oracle evidence.
- Do not copy hidden solution logic or acceptance values into public inputs.
- Changes to 032 and 033 may proceed independently once shared verifier and
  state-field names are frozen.

## Release Gates

Before release, each case must have:

1. passing construction-contract and public-isolation tests;
2. passing smoke execution in the declared environment;
3. verifier unit tests plus positive, negative, missing, and tampered fixtures;
4. one complete hidden reference run that passes the paper-profile verifier;
5. immutable source, environment, input, and oracle-evidence hashes;
6. generated validation state with `construction_valid=true`,
   `smoke_validated=true`, and `oracle_calibrated=true`.

No successful evaluated-agent run is required for release.
