---
name: build-scientific-benchmark-case
description: Use when a user explicitly asks to construct, validate, discover, refine, release-check, or hand off an auditable scientific-agent benchmark case from papers, SI, repositories, datasets, models, or calculation artifacts.
disable-model-invocation: true
---

# Build Scientific Benchmark Case

Turn a paper ecosystem into a complete, auditable benchmark case. The Skill is
**explicit-only**: it runs when the user invokes
`/build-scientific-benchmark-case`. It never auto-constructs cases, creates
files, or starts computation.

## Command model

```text
/build-scientific-benchmark-case
    mode: intake | extract-spec | design | scaffold | mvp | construct |
          validate | discovery | release-check | experiment-handoff
    category: mlp
```

Modes are lifecycle operations of one Skill, not separate skills:

| Mode | Purpose | Mutating? |
|---|---|---|
| `intake` | Identify objective, category, source materials and repository contract | No |
| `extract-spec` | Build evidence-backed category reproduction/spec artifacts | Writes analysis artifacts |
| `design` | Define case objective, execution class, observables, public/hidden split and gates | Writes case design |
| `scaffold` | Create a new draft case tree from a validated design — `draft` and nothing more. Optional `--target dftworld` renders the executable draft (task.toml v1.2, Dockerfile, target lock) through the dftworld Case Factory CLI; outside the dftworld repository, or when the adapter rejects the design, it fails clearly and preserves the portable scaffold | Writes new case directory |
| `mvp` | One pass through intake → extract-spec → design → scaffold → minimal construct → MVP gate (`scripts/common/check_discovery_runnable.py`). Default outputs: case tree, `MVP-READINESS.json`, `DISCOVERY-RUNBOOK.md`. Pauses at scientific target choices, unclear license/downloads, container builds, real scheduler submissions, expensive training/DFT/MD, and destructive overwrites. Never emits ablation or release packets | Writes new case tree; local checks only |
| `construct` | Build reference, solution, verifier, fixtures and profiles through gated stages | Potentially expensive; approval-bound |
| `validate` | Run structural/scientific/adversarial checks and derive open gates. `runnable_draft` derives only from `check_discovery_runnable.py --derive-state` | Runs checks only unless approved |
| `discovery` | Classify one real Runnable Draft attempt before expert-reference investment. The first real run uses the smoke-class Discovery profile from `DISCOVERY-RUNBOOK.md`, never `formal` | Runs only when separately authorized |
| `release-check` | Fail-closed derivation of `benchmark_valid` | Writes derived release state |
| `experiment-handoff` | Freeze a valid case for pilot/ablation | Valid cases only |

## Router

1. **Resolve category.** Read `references/category-registry.yaml`. If the
   requested category is absent, stop and report `supported categories: <list>`.
   Never fall back to MLP or a guessed generic schema.
2. **Load Common Core references.** Load `references/common/` policy files:
   case standard, lifecycle and gates, `mvp-runnable-draft.md` (the one
   authoritative `runnable_draft` definition), public/hidden boundary,
   reference/solution policy, verifier and fixture policy, threshold
   calibration, evidence retention, execution classes, experiment handoff.
   For Draft admission or regeneration, also read
   `cross-layer-consistency.md`. For Discovery or refinement, read
   `discovery-and-refinement.md` and `failure-taxonomy.yaml`.
3. **Load only the selected category references.** Resolve
   `references_root`, `scripts_root`, and `template_root` from the registry
   entry for that category. Do not load other categories into context.
4. **Dispatch on mode.** Each mode has a contract described in the Common Core
   references. Expensive execution, destructive cleanup, and material
   scientific-target choices pause for explicit user authorization.

For `discovery`, read `references/common/failure-taxonomy.yaml` and run
`scripts/common/classify_failure.py` on the durable run record. A failed run is
evidence; classify source, infrastructure, case-design, runtime, resource, and
Agent limitations before deciding REJECT, REFINE, or PROMOTE.

## extract-spec (category mlp)

Preserves the portable v1.1 extraction semantics. Category scripts:

```text
${CLAUDE_SKILL_DIR}/scripts/categories/mlp/validate_spec.py
${CLAUDE_SKILL_DIR}/scripts/categories/mlp/check_readiness.py
${CLAUDE_SKILL_DIR}/scripts/categories/mlp/hash_sources.py
```

Artifacts remain `source-evidence-map.yaml`, `mlp-reproduction-spec.yaml`,
`reproducibility-assessment.yaml`, and `sources.lock.json`.

The MLP category owns model roles, dataset/label/reference-method fingerprint,
implementation, architecture, training, validation, access/license, readiness,
and source conflicts. Common Core does not interpret these fields.

For MLP design/construct/validate work, read
`references/categories/mlp/prompt-contract.md`. `runnable_draft` is derived
only by `scripts/common/check_discovery_runnable.py CASE` (it orchestrates
`check_draft_consistency.py`, semantic spec validation, real packaging, and
the verifier mount smoke — see `references/common/mvp-runnable-draft.md`).
A tree that merely parses is `draft`, not runnable.

## State and entry rules

- `scaffold` produces `draft` only; only
  `scripts/common/check_discovery_runnable.py --derive-state` may write
  `runnable_draft` (single authoritative definition:
  `references/common/mvp-runnable-draft.md`).
- A case's `tests/test.sh` is the hidden-verifier container entry and only
  runs `tests/verifier.py` against the sealed submission; it must always emit
  the common `result.json`. Construction self-tests
  (`tests/test_verifier_contract.py`, `tools/` linters such as
  `validate_submission_manifest.py`) are build-time developer tools and are
  never wired into `test.sh`.
- No script may depend on being executed from the case root; the mount layout
  (`/tests`, sealed root, result dir) defines the contract.
- Semantic validators run as scripts with the repository/runtime interpreter;
  a YAML/Ruby syntax parse never substitutes for one, and a validator that
  was not run or failed blocks the state.

## Non-negotiable gates

- Only deterministic release derivation may write `benchmark_valid=true`.
- Candidate-visible content is built from a positive allowlist; hidden
  reference, solution, tests, thresholds, fixtures, old runs, Git metadata,
  host state, and credentials are physically absent.
- Thresholds freeze only from independent calibration evidence collected
  before formal Agent results are inspected.
- No downloads, training, labeling, MD, container builds, remote mutations, or
  HPC submission without explicit authorization for that execution step.
- Experiment handoff is unavailable until the case is independently valid.
