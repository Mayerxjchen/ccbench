# Scientific Benchmark Case Builder Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-purpose `literature-to-mlp-spec` command with one portable Claude Code skill, `build-scientific-benchmark-case`, that owns the complete lifecycle for constructing auditable scientific-agent benchmark cases. MLP is the only category implemented in v2.0; future scientific categories plug into the same common contracts without creating new top-level skills.

**Architecture:** The Skill has one explicit entrypoint and two internal layers. Common Core owns case lifecycle, directory scaffolding, Candidate/Verifier trust boundaries, execution classes, reference/solution policy, fixture/threshold/evidence contracts, release derivation, and experiment handoff. Category modules own scientific intake schemas, source extraction, category-specific workflow capabilities, verifier-layer proposals, and category validators. The MLP module absorbs all useful `literature-to-mlp-spec` functionality and adds full 034-class case construction.

**Tech Stack:** Claude Code Agent Skills, Python 3.12+, PyYAML, JSON/TOML, SHA-256, unittest/pytest, Docker/Apptainer execution contracts, optional Slurm/HPC adapters.

## Global Constraints

- There is exactly one routable Skill and one Claude Code command: `/build-scientific-benchmark-case`.
- The Skill uses `disable-model-invocation: true`; case construction begins only through explicit user invocation.
- `mlp` is a category identifier inside the Skill, not another routable Skill.
- Existing `literature-to-mlp-spec` behavior becomes the MLP category's `extract-spec` mode and supporting references/scripts.
- Do not modify Case 034 while implementing the Skill. Use a compact 034-like fixture that captures its structure and failure lessons.
- Case 034 is not a release template: its authoritative state remains `benchmark_valid=false`.
- Never turn Water64, ai2-kit, CP2K AIMD, active learning, hidden NVT, RDF, or L0–L9 into universal requirements.
- Common Core contains no MLP-specific fields, model families, scientific metrics, or training assumptions.
- A new category is accepted only through the category contract and registry; unknown categories fail with a supported-category list.
- The Builder may write `draft`, `constructed`, and component-validation states. Only deterministic release derivation may write `benchmark_valid=true`.
- Never run downloads, model training, labeling, MD, container builds, remote mutations, or HPC jobs without explicit authorization for that execution step.
- Smoke validates plumbing only and never establishes scientific success.
- Candidate-visible content is built from a positive allowlist. Hidden reference, solution, verifier/tests, thresholds, fixtures, old runs, Git metadata, host state, and credentials are physically absent.
- Candidate and Verifier are always separate trust domains. Local compute remains sandboxed; the Candidate is destroyed before a fresh hidden Verifier runs.
- Expert solution structure is not the required Agent implementation. At least one alternative-valid fixture must prove outcome-based scoring.
- Thresholds freeze only from independent calibration evidence collected before formal Agent results are inspected.
- Large evidence uses a restorable durable-bundle contract; workspaces are caches after bundle verification.
- No-Skill/With-Skill experiments are outside core case construction and remain blocked until the case is independently valid.

## Single-Skill Command Model

```text
/build-scientific-benchmark-case
    mode: intake | extract-spec | design | scaffold | construct |
          validate | release-check | experiment-handoff
    category: mlp
```

Modes are lifecycle operations, not separate skills:

| Mode | Purpose | Mutating? |
|---|---|---|
| `intake` | Identify objective, category, source materials and repository contract | No |
| `extract-spec` | Build evidence-backed category reproduction/spec artifacts | Writes analysis artifacts |
| `design` | Define case objective, execution class, observables, public/hidden split and gates | Writes case design |
| `scaffold` | Create a new draft case tree from validated design | Writes new case directory |
| `construct` | Build reference, solution, verifier, fixtures and profiles through gated stages | Potentially expensive; approval-bound |
| `validate` | Run structural/scientific/adversarial checks and derive open gates | Runs checks only unless approved |
| `release-check` | Fail-closed derivation of `benchmark_valid` | Writes derived release state |
| `experiment-handoff` | Freeze a valid case for pilot/ablation | Valid cases only |

## Target Portable Package

```text
scientific-benchmark-case-builder-portable/
├── skills/
│   └── build-scientific-benchmark-case/
│       ├── SKILL.md
│       ├── references/
│       │   ├── common/
│       │   │   ├── case-standard.md
│       │   │   ├── lifecycle-and-gates.md
│       │   │   ├── public-hidden-boundary.md
│       │   │   ├── reference-and-solution-policy.md
│       │   │   ├── verifier-and-fixture-policy.md
│       │   │   ├── threshold-calibration.md
│       │   │   ├── evidence-retention-policy.md
│       │   │   ├── execution-classes.md
│       │   │   └── experiment-handoff.md
│       │   ├── categories/
│       │   │   └── mlp/
│       │   │       ├── reproduction-schema.md
│       │   │       ├── source-evidence-policy.md
│       │   │       ├── target-model-policy.md
│       │   │       ├── readiness-policy.md
│       │   │       ├── workflow-capabilities.md
│       │   │       └── verifier-policy.md
│       │   └── category-registry.yaml
│       ├── scripts/
│       │   ├── common/
│       │   │   ├── init_case.py
│       │   │   ├── validate_case.py
│       │   │   ├── audit_candidate_bundle.py
│       │   │   ├── derive_validation_state.py
│       │   │   ├── freeze_evaluator_manifest.py
│       │   │   └── check_release.py
│       │   └── categories/
│       │       └── mlp/
│       │           ├── validate_spec.py
│       │           ├── check_readiness.py
│       │           ├── hash_sources.py
│       │           ├── derive_verifier_plan.py
│       │           └── validate_category.py
│       └── assets/
│           └── case-template/
│               ├── common/
│               ├── execution/
│               │   ├── local-sandbox/
│               │   └── hpc-controller/
│               └── categories/
│                   └── mlp/
├── tests/
│   ├── test_portable_package.py
│   ├── test_common_builder.py
│   ├── test_mlp_category.py
│   └── fixtures/
│       ├── simple-mlp-local/
│       ├── water64-like-hpc/
│       ├── blocked-mlp/
│       ├── verifier-gaming/
│       ├── alternative-valid-layout/
│       └── dummy-future-category/
├── README.md
├── install.sh
├── manifest.json
└── SHA256SUMS
```

---

### Task 1: Freeze the one-Skill/category architecture

**Files:**
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/category-registry.yaml`
- Create: `scientific-benchmark-case-builder-portable/tests/test_portable_package.py`
- Create: `scientific-benchmark-case-builder-portable/tests/test_common_builder.py`

**Interfaces:**
- Exactly one Skill directory and one frontmatter name.
- `category-registry.yaml` maps a category ID to its references, validators,
  template overlay, supported case kinds and verifier capabilities.

- [ ] **Step 1: Write architecture RED tests**

```python
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SKILLS = PACKAGE / "skills"
BUILDER = SKILLS / "build-scientific-benchmark-case"


def test_package_exposes_exactly_one_skill():
    skill_dirs = [p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()]
    assert skill_dirs == [BUILDER]


def load_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


def test_builder_is_explicit_only():
    frontmatter = load_frontmatter(BUILDER / "SKILL.md")
    assert frontmatter["name"] == "build-scientific-benchmark-case"
    assert frontmatter["disable-model-invocation"] is True
```

- [ ] **Step 2: Define category plugin contract**

```yaml
schema_version: 1
categories:
  mlp:
    state: supported
    case_kinds:
      - final_model_retraining
      - end_to_end_model_development
      - published_model_execution
      - model_evaluation
      - active_learning_workflow
    references_root: references/categories/mlp
    scripts_root: scripts/categories/mlp
    template_root: assets/case-template/categories/mlp
```

Unknown categories fail with `supported categories: mlp`; they never fall back
to MLP or a generic guessed schema.

- [ ] **Step 3: Implement the Skill router**

The entrypoint determines mode/category, loads Common Core references, then
loads only the selected category references. It does not load all current or
future categories into context.

---

### Task 2: Migrate portable v1.1 into the MLP category

**Files:**
- Move: `literature-to-mlp-spec-portable/skills/literature-to-mlp-spec/references/reproduction-schema.md` -> `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/reproduction-schema.md`
- Move: `literature-to-mlp-spec-portable/skills/literature-to-mlp-spec/references/source-evidence-policy.md` -> `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/source-evidence-policy.md`
- Move: `literature-to-mlp-spec-portable/skills/literature-to-mlp-spec/references/target-model-policy.md` -> `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/target-model-policy.md`
- Move: `literature-to-mlp-spec-portable/skills/literature-to-mlp-spec/references/readiness-policy.md` -> `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/readiness-policy.md`
- Move: existing MLP helper scripts into `scripts/categories/mlp/`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/workflow-capabilities.md`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/verifier-policy.md`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/categories/mlp/validate_category.py`

**Interfaces:**
- `extract-spec --category mlp` preserves v1.1 output semantics.
- MLP category artifacts remain `source-evidence-map.yaml`,
  `mlp-reproduction-spec.yaml`, `reproducibility-assessment.yaml`, and
  `sources.lock.json`.

- [ ] **Step 1: Copy existing v1.1 tests before moving code**

Move and adapt all 12 verified regression tests. Confirm they fail only because
the new category paths and router do not exist.

Preserve v1.1 draft safety through this explicit disposition table:

| v1.1 artifact | v2.0 disposition |
|---|---|
| `scripts/build_benchmark_draft.py` | Superseded; its gate logic moves into `design` mode and `scripts/common/init_case.py` |
| `references/benchmark-case-policy.md` | Merged into `references/common/case-standard.md` and `lifecycle-and-gates.md` |
| `references/research-handoff.md` | Split into Common lifecycle/evidence policy and `experiment-handoff.md` |
| `benchmark-case-draft.yaml` | Replaced by the canonical `case-design.yaml` artifact |

Adapt the existing draft regression into:

```python
def test_scaffold_refuses_blocked_design_and_never_sets_valid(tmp_path):
    proc = run_init_case(BLOCKED_DESIGN, tmp_path / "case", check=False)
    assert proc.returncode != 0
    assert not (tmp_path / "case/benchmark_valid.json").exists()
```

An explicit `--allow-blocked` option may scaffold a draft with all blockers
preserved, but `benchmark_valid` remains false.

- [ ] **Step 2: Preserve MLP-specific responsibilities**

The category owns model roles, dataset/label/reference-method fingerprint,
implementation, architecture, training, validation, access/license, readiness,
and source conflicts. Common Core must not interpret these fields.

- [ ] **Step 3: Add MLP case-kind capability mapping**

Examples:

```text
final_model_retraining
  -> dataset binding + model training + hidden model-level verification

end_to_end_model_development
  -> source structure + reference labeling + training + applicable iterative
     workflow + static/dynamic/property validation

active_learning_workflow
  -> explore/select/label/grow/retrain lineage is a hard outcome

published_model_execution
  -> immutable model + runtime + target observable; training may be N/A

model_evaluation
  -> immutable or reproducibly trained model + evaluation split + metric
     implementation; training is required only when the model is not released
```

---

### Task 3: Define Common Core case lifecycle and directory standard

**Files:**
- Create: `references/common/case-standard.md`
- Create: `references/common/lifecycle-and-gates.md`
- Create: `scripts/common/validate_case.py`

**Interfaces:**
- Maturity states: `draft`, `constructed`, `reference_validated`,
  `verifier_validated`, `benchmark_valid`, `experiment_ready`.
- Common release groups are category-neutral.

- [ ] **Step 1: Write RED lifecycle tests**

Reject unknown states, backward promotion, self-asserted validity, open release
gates with `benchmark_valid=true`, and missing evidence pointers. The
`validate_case.py` structural entrypoint must also reject:

```text
unknown execution class
hpc_controller without resource profile, platform profile, or compute-runtime lock
local_sandbox containing site hostname, partition, account, SSH, or scheduler fields
```

- [ ] **Step 2: Define common gate groups**

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

Categories add sub-gates without adding new top-level lifecycle semantics.

---

### Task 4: Centralize complete draft templates and deterministic scaffolding

**Files:**
- Create: `scripts/common/init_case.py`
- Create: `assets/case-template/common/CONTRACT.md`
- Create: `assets/case-template/common/instruction.md`
- Create: `assets/case-template/common/task.toml`
- Create: `assets/case-template/common/case-design.yaml`
- Create: `assets/case-template/common/source/source.lock.json`
- Create: `assets/case-template/common/reference/reference.json`
- Create: `assets/case-template/common/reference/thresholds.json`
- Create: `assets/case-template/common/solution/expert/README.md`
- Create: `assets/case-template/common/tests/fixture-matrix.yaml`
- Create: `assets/case-template/common/profiles/smoke.yaml`
- Create: `assets/case-template/common/profiles/formal.yaml`
- Create: `assets/case-template/common/evidence/retention-policy.yaml`
- Create: `assets/case-template/common/evidence/manifest.json`
- Create: `assets/case-template/common/evaluator-manifest.json`
- Create: `assets/case-template/common/VALIDATION.json`
- Create: `assets/case-template/common/benchmark_valid.json`
- Create: `assets/case-template/execution/local-sandbox/runtime.yaml`
- Create: `assets/case-template/execution/hpc-controller/execution.yaml`
- Create: `assets/case-template/execution/hpc-controller/profiles/resource.yaml`
- Create: `assets/case-template/execution/hpc-controller/profiles/platform.yaml`
- Create: `assets/case-template/execution/hpc-controller/reference/compute-runtime.lock.json`
- Create: `assets/case-template/categories/mlp/case-requirements.yaml`
- Create: `assets/case-template/categories/mlp/verifier-plan.yaml`

**Interfaces:**
- `init_case.py --category CATEGORY --kind KIND --design DESIGN --output CASE`.
- Refuses a non-empty output directory without explicit overwrite authorization.
- Applies overlays in order: common -> execution class -> category.

- [ ] **Step 1: Write scaffold RED tests**

Every case contains:

```text
CONTRACT.md
instruction.md
task.toml
case-design.yaml
public/
source/
reference/
solution/expert/
tests/fixtures/{positive,negative,alternative-valid}/
profiles/{smoke,formal}/
tools/
evidence/{retention-policy.yaml,manifest.json}
evaluator-manifest.json
VALIDATION.json
benchmark_valid.json
```

- [ ] **Step 2: Make Task 4 the sole template owner**

Every later task modifies these valid draft-state assets; no later task creates
a path already expected by scaffold tests. Draft documents use explicit null,
unknown, draft, and blocker records rather than unfinished markers.

The HPC scaffold test must additionally require non-empty draft schemas at:

```text
profiles/resource.yaml
profiles/platform.yaml
reference/compute-runtime.lock.json
```

The Local scaffold test requires all three HPC-only files and every site-specific
hostname/partition/account/SSH field to be absent.

- [ ] **Step 3: Verify deterministic overlays**

Two fresh runs from the same design produce identical structural manifests.
Adding a future category overlay must not change Common Core or MLP output.
Also test overwrite refusal: a non-empty output directory without explicit
authorization must fail and every pre-existing byte must remain unchanged.

---

### Task 5: Enforce the universal Candidate/Verifier trust boundary

**Files:**
- Create: `references/common/public-hidden-boundary.md`
- Create: `scripts/common/audit_candidate_bundle.py`
- Create: `tests/fixtures/verifier-gaming/`

**Interfaces:**
- Candidate bundle is generated/audited from positive allowlist rules.
- Local and HPC cases use the same hidden-evaluator lifecycle.

- [ ] **Step 1: Write adversarial RED tests**

Reject hidden reference, solution, tests, thresholds, fixtures, expert metrics,
Git/host state, old runs, credentials, symlinks/hardlinks/path traversal, and
answer-specific semantic leakage in public files.

- [ ] **Step 2: Define official local isolation**

`local_sandbox` means isolated local compute inside the Candidate sandbox. An
unrestricted evaluator-host process is development-only and cannot generate
official adversarial evidence. Candidate is destroyed before fresh Verifier.

- [ ] **Step 3: Check prompt/verifier fidelity**

Every hard outcome must be stated abstractly in the instruction, while expert
implementation details remain hidden when alternative methods are allowed.

---

### Task 6: Construct reference and expert solution through common policy

**Files:**
- Create: `references/common/reference-and-solution-policy.md`
- Populate common reference/solution templates from Task 4
- Extend: `references/categories/mlp/workflow-capabilities.md`

**Interfaces:**
- Reference states: `planned -> inputs_frozen -> smoke_executed ->
  formal_executed -> independently_verified -> reproducible`.
- Category capability mapping determines required lineage.

- [ ] **Step 1: Require runnable provenance**

Archived outputs without executable inputs, runtime identity, commands and an
independent parser cannot become a reproducible reference.

- [ ] **Step 2: Apply MLP lineage conditionally**

- Final retraining: dataset -> config -> model -> held-out metrics.
- Active-learning case: explore -> select -> label -> dataset growth -> retrain.
- Published model: artifact -> runtime -> inference/observable.
- 034-like end-to-end: structure -> preparation -> DFT reference -> training ->
  iterative improvement -> static/dynamic/property validation.

- [ ] **Step 3: Keep execution approval-bound**

The Skill may construct plans, scripts and preflight checks. It pauses before
downloads with unclear rights, container builds, expensive calculation, remote
mutation or scheduler submission unless already authorized.

---

### Task 7: Build category-driven verifier and fixture planning

**Files:**
- Create: `references/common/verifier-and-fixture-policy.md`
- Create: `scripts/categories/mlp/derive_verifier_plan.py`
- Create: `scripts/common/generate_fixture_matrix.py`
- Create: `tests/fixtures/alternative-valid-layout/`

**Interfaces:**
- Category module proposes applicable outcomes; Common Core requires evidence,
  negative coverage and alternative validity.

- [ ] **Step 1: Define selectable MLP verifier layers**

```text
MLP-V0 system/submission identity
MLP-V1 data/label provenance
MLP-V2 model authenticity
MLP-V3 iterative workflow integrity (conditional)
MLP-V4 hidden static accuracy
MLP-V5 hidden dynamic stability (conditional)
MLP-V6 hidden physical observable (conditional)
```

Common layers are named and tested independently of category science:

```text
C-V7 resource and provenance compliance
C-V8 submission integrity, filesystem safety, and manifest contract
```

- [ ] **Step 2: Require fixture matrix closure**

One positive expert fixture, at least one alternative-valid fixture, and one
negative per hard outcome. Include gaming fixtures appropriate to the category.

- [ ] **Step 3: Prevent expert-path scoring**

Alternative-valid fixtures change internal layout, valid method choice or stage
count while preserving outcomes. Exact expert directory names/scripts are never
required.

---

### Task 8: Calibrate thresholds and hidden validation independently

**Files:**
- Create: `references/common/threshold-calibration.md`
- Populate Task 4 threshold template
- Extend: `references/categories/mlp/verifier-policy.md`
- Create: `scripts/common/check_threshold_freeze.py`

**Interfaces:**
- Threshold states: `draft`, `calibrating`, `frozen`.
- Freeze record contains reference run IDs/digests, statistics, units, rationale,
  timestamps and case/verifier versions.

- [ ] **Step 1: Write fail-closed tests**

Reject one-run stochastic calibration, post-Agent threshold timestamps,
threshold writers inside expert runners, missing units/metric definitions, and
an expert value copied directly as a tight pass bound.

- [ ] **Step 2: Define hidden-set independence**

Record generation provenance, Candidate inaccessibility and submission overlap
checks. Expert mother datasets may seed hidden data only when the access and
overlap arguments are independently testable.

---

### Task 9: Add execution classes, evidence retention and release derivation

**Files:**
- Create: `references/common/execution-classes.md`
- Create: `references/common/evidence-retention-policy.md`
- Create: `scripts/common/derive_validation_state.py`
- Create: `scripts/common/freeze_evaluator_manifest.py`
- Create: `scripts/common/check_release.py`
- Populate Task 4 profiles/evidence templates

**Interfaces:**
- `local_sandbox` and `hpc_controller` share trust/release contracts but retain
  different compute paths.
- Only `check_release.py` can derive `benchmark_valid=true`.

- [ ] **Step 1: Test execution separation**

Simple MLP Local fixture contains no HPC/SSH/site fields. Water64-like fixture
requires batch/GPU/fetch capabilities and rejects simulated Slurm as formal
evidence.

- [ ] **Step 2: Define durable evidence**

Git stores manifests; large bytes use content-addressed primary and replica
objects. Finalization requires upload, read-back hash, fresh restore and
reverification. `evidence/manifest.json` binds every gate to restorable bytes.

- [ ] **Step 3: Test release failure classes**

Missing byte, hash mismatch, open gate, draft threshold, evaluator drift,
Candidate leak, insufficient references or non-restorable bundle all force
invalidity. Infrastructure invalidity is not a scientific failure.

---

### Task 10: Prove future category extensibility without implementing one

**Files:**
- Create: `tests/fixtures/dummy-future-category/category.yaml`
- Extend: `tests/test_common_builder.py`

**Interfaces:**
- A test-only category registers a schema/overlay/validator without modifying
  Common Core files.

- [ ] **Step 1: Write extension test**

Register `dummy` only in a temporary registry and scaffold it with a minimal
overlay. Assert Common Core lifecycle/trust/evidence files are unchanged and no
MLP fields appear.

- [ ] **Step 2: Keep production registry MLP-only**

The shipped registry lists exactly `mlp`. The dummy category is test data and is
never installed as supported functionality.

This task proves architecture extensibility without prematurely designing DFT,
MD, quantum-chemistry or analysis categories.

---

### Task 11: Package migration and Claude Code installation

**Files:**
- Rename package root to `scientific-benchmark-case-builder-portable/`
- Modify: `install.sh`, `manifest.json`, `README.md`, `SHA256SUMS`
- Create/modify package tests listed above

**Interfaces:**
- Portable package version: `2.0.0`.
- Installs exactly one Skill at
  `~/.claude/skills/build-scientific-benchmark-case/`.
- Does not automatically delete a legacy
  `~/.claude/skills/literature-to-mlp-spec/` installation.

- [ ] **Step 1: Add migration notice**

Document:

```text
old command: /literature-to-mlp-spec
new command: /build-scientific-benchmark-case mode=extract-spec category=mlp
```

Provide an explicit `--remove-legacy` installer option; default installation
preserves the old directory and warns about it.

- [ ] **Step 2: Preserve atomic installation**

Stage, validate and atomically replace the new Skill. A failed copy/validation
leaves the previous installation intact.

- [ ] **Step 3: Verify package contract**

All files except `SHA256SUMS` are listed exactly once. Package tests validate
frontmatter, name/path agreement, internal reference closure, registry closure,
script syntax and absence of unfinished scaffold markers.

---

### Task 12: End-to-end MLP category acceptance

**Files:**
- Complete: `tests/test_mlp_category.py`
- Complete all MLP fixtures

- [ ] **Step 1: Run deterministic flows**

```text
simple MLP paper -> extract-spec -> local case -> constructed, invalid pending reference
Water64-like paper -> end-to-end HPC case -> full capability plan, invalid pending evidence
blocked sources -> draft case with blockers only
alternative-valid layout -> verifier plan accepts outcome
gaming fixture -> boundary/verifier plan rejects
```

- [ ] **Step 2: Run authorized Claude Code forward tests**

Compare without/with Skill only when explicitly authorized. Required behavior:
no invented parameters/thresholds, no hidden leak, no automatic execution,
034-like complexity without hardcoded Water64 constants, invalid incomplete
cases, and implementation-independent acceptance.

- [ ] **Step 3: Final verification**

```bash
uv run python -m unittest -v scientific-benchmark-case-builder-portable/tests/test_portable_package.py
uv run python -m unittest -v scientific-benchmark-case-builder-portable/tests/test_common_builder.py
uv run python -m unittest -v scientific-benchmark-case-builder-portable/tests/test_mlp_category.py
bash scientific-benchmark-case-builder-portable/install.sh --check
bash -n scientific-benchmark-case-builder-portable/install.sh
```

Expected: one installed Skill, MLP-only production registry, passing local/HPC
fixtures, complete checksum coverage, and no path capable of asserting validity
without full reference/verifier/evidence gates.

---

### Task 13: Gate experiment handoff after case validity

**Files:**
- Create: `references/common/experiment-handoff.md`

- [ ] **Step 1: Require frozen experiment identity**

Only `benchmark_valid=true` cases may produce experiment packets. Freeze case,
instruction/public, Skill treatment, Agent/model, runtime, resource/platform,
Verifier, evidence and failure taxonomy.

- [ ] **Step 2: Keep ablation out of default scaffold**

Do not create `ablation/` during case construction. Add pilot/formal experiment
files only in `experiment-handoff` mode. Pilot results remain excluded from
formal statistics.

---

## Definition of Done

The total Skill is complete only when:

- Claude Code exposes exactly `/build-scientific-benchmark-case`.
- The only shipped category is `mlp`; MLP extraction behavior from portable
  v1.1 remains available through `mode=extract-spec`.
- A validated MLP spec can scaffold either Local or HPC case structure.
- Generated cases include source/public/reference/solution/tests/profiles,
  evidence, evaluator manifest, validation state and release state.
- Candidate bundle audit proves hidden assets are absent.
- MLP case kinds select only applicable workflow/verifier requirements.
- A 034-like fixture receives end-to-end capabilities without exporting its
  Water64/ai2-kit constants into Common Core.
- Expert reference must be runnable and independently parsed.
- Verifier planning includes negative and alternative-valid fixtures.
- Thresholds cannot freeze without independent calibration.
- Evidence is restorable and hash-bound to every release gate.
- `benchmark_valid` is fail-closed and deterministically derived.
- Expensive/local-remote execution remains permission-gated.
- A dummy category proves future extensibility without being shipped.
- Experiment handoff remains blocked until case validity.

Final workflow:

```text
/build-scientific-benchmark-case category=mlp mode=intake
    -> extract-spec
    -> design
    -> scaffold
    -> construct reference + verifier + fixtures
    -> validate + calibrate + seal evidence
    -> release-check
    -> experiment-handoff
```
