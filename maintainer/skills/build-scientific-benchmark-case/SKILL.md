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
