---
name: build-scientific-benchmark-case
description: Maintainer skill to intake, design, build, validate, discover, release, and publish auditable CCBench cases.
disable-model-invocation: true
---

# Build Scientific Benchmark Case (v3)

Authoritative case construction workflow for CCBench maintainers.

All deterministic state transitions, Case IR validations, and verifier compilation
are delegated directly to `ccbench.builder`. The LLM operates purely as an
interactive assistant and reasoner; it is strictly prohibited from writing or
fabricating lifecycle state directly.

## Command Model

```text
/build-scientific-benchmark-case
    mode: intake | design | build | validate | discovery | release | publish
    category: mlp  # (production-supported category; spectroscopy/catalysis planned)
```

### The 7 User Modes

| Mode | Purpose | Invoked Command / Target | Mutating? |
|---|---|---|---|
| `intake` | Analyze paper, repository, datasets and generate `source/intake.json` + `sources.lock.json` | `ccbench case intake` | Writes source workspace |
| `design` | Author and validate Case IR SSOT (`design/case.ir.yaml`) against schema | `ccbench case design` | Writes Case IR |
| `build` | Compile Case IR into draft files (`task.md`, `case.toml`, `verifier/`) | `ccbench case build` | Compiles draft |
| `validate` | Run verifier mount smoke and derive `RUNNABLE_DRAFT` | `ccbench case validate` | Local smoke run |
| `discovery` | Record discovery classification (`PROMOTED`, `REFINE`, `REJECT`) | `ccbench case discovery` | Updates discovery |
| `release` | Check benchmark release criteria and derive `BENCHMARK_VALID` | `ccbench case release-check` | Release audit |
| `publish` | Atomically publish valid case into canonical `cases/<case-id>` | `ccbench case publish` | Publishes 4 objects |

## Workspace Isolation

Draft case runs are strictly isolated under:
`maintainer/builder/runs/<run-id>/`

The production benchmark directory `cases/` is strictly a release package target,
and contains ONLY the four canonical objects:
1. `task.md`
2. `input/`
3. `verifier/`
4. `case.toml`

Maintainer sources, design artifacts, and discovery evidence remain safely in `maintainer/`.

## Anti-Leakage & Taint Rules

1. All sources are classified into `PUBLIC_SOURCE`, `MAINTAINER_SOURCE`, `GOLD_SOURCE`, or `REFERENCE_SOURCE`.
2. Candidate-visible files (`input/`, `task.md`) must NEVER contain or derive from `GOLD_SOURCE` assets.
3. Verifier checks and hidden references must remain strictly outside the candidate's workspace.

## Core Maintainer Governance Policies

### 1. Scientific Identity (`paper_faithful` vs `benchmark_adaptation`)
- **Published Evidence SSOT**: Observables, crystal structures, and reference values derived from published literature must be anchored to cited papers/DOIs.
- **No Fabricated Evidence**: Approximated, re-scaled, or simulated proxy values must be explicitly classified as `benchmark_adaptation` and never masquerade as published experimental evidence.

### 2. Execution Authorization Boundaries
- The LLM builder is strictly an offline reasoning agent. It is forbidden from initiating the following operations without explicit maintainer authorization:
  - External network downloads or dataset pulls.
  - Container image builds or docker tag pushes.
  - Expensive DFT, finite MD trajectories, or model training jobs (>60s CPU/GPU).
  - Real HPC cluster job dispatches via slurm/compshare.

### 3. Discovery Failure Attribution
- **Failed Run ≠ Agent Limitation**: Maintainers must not prematurely blame candidate agents for failure.
- Failures must be systematically attributed according to the scientific failure taxonomy:
  - `SOURCE_BLOCKED`: Missing paper data, ambiguous units, corrupted raw inputs.
  - `RUNTIME_BLOCKED`: Package version incompatibilities, missing system libraries in container.
  - `RESOURCE_BLOCKED`: OOM, scheduler timeouts, disk space exhaustion.
  - `CASE_DESIGN_BLOCKED`: Ambiguous prompt instructions, impossible threshold bounds.
  - `INFRA_INVALID`: Network disconnection, gateway crash, host platform error.
  - `AGENT_LIMITATION`: Legitimate benchmark failure where environment, inputs, and verifiers were valid.

### 4. Threshold Independence
- Verification thresholds in `verification.thresholds` must be frozen **before** inspecting formal candidate agent transcripts or outcomes.
- `threshold-freeze.json` records `formal_agent_results_seen: false`. Adapting benchmark thresholds to accommodate a specific agent's performance constitutes data contamination and is fail-closed rejected.

