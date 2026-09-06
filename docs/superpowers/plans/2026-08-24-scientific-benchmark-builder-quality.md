# Scientific Benchmark Builder Quality Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the portable benchmark-case-builder skill with reusable Runnable Draft, Discovery, Prompt-quality, cross-layer consistency, stale-artifact, and fail-closed reference/Solution controls, then remove only its Claude Code installed copy.

**Architecture:** Keep the existing Common Core plus category-plugin layout. Add Common Discovery and cross-layer policy references, an MLP Prompt contract, a deterministic MLP draft-consistency checker driven by `public/system.json`, and a Discovery failure classifier. Tests encode generic failures rather than Case 042 identities or chemistry.

**Tech Stack:** Markdown/YAML skill resources, Python 3 standard library plus PyYAML/NumPy already used by the package, pytest, Bash packaging scripts.

## Global Constraints

- The portable repository source is authoritative; the Claude Code copy is removed only after verification.
- No test or implementation dispatches on case number, paper, chemistry, or model name.
- Runnable Draft remains `benchmark_valid=false`; expert reference and frozen thresholds remain post-PROMOTE work.
- Validators are read-only and fail closed; regeneration is an explicit separate operation.
- Existing package behavior and all 95 baseline tests remain green.
- Do not stage or commit unrelated parent-workspace changes.

---

### Task 1: RED tests for lifecycle and Discovery classification

**Files:**
- Create: `scientific-benchmark-case-builder-portable/tests/test_discovery_quality.py`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/failure-taxonomy.yaml`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/classify_failure.py`

**Interfaces:**
- Produces: `classify(run: dict) -> dict` with `blocked_kind`, `decision`, `evidence`, `confidence`.
- Consumes: a Discovery run record with result/failure codes, verifier layers, telemetry, and optional source/runtime/resource/case-design evidence.

- [ ] **Step 1: Write failing tests for missing Discovery resources and classification priority**

Add tests that import `classify_failure.py` and assert:

```python
assert classify({"failure_code": "INFRA_INVALID"})["blocked_kind"] == "INFRA_INVALID"
assert classify({"source": {"critical_conflict": True}})["decision"] == "REJECT"
assert classify({"runtime": {"available": False}})["blocked_kind"] == "RUNTIME_BLOCKED"
assert classify({"telemetry": {"oom": True}})["blocked_kind"] == "RESOURCE_BLOCKED"
assert classify({"case_design": {"cross_layer_valid": False}})["blocked_kind"] == "CASE_DESIGN_BLOCKED"
```

Also assert `SKILL.md` exposes `discovery` and references the taxonomy.

- [ ] **Step 2: Run RED tests**

Run: `/Users/chenxuanjie/案例测试/dftworld2/.venv/bin/python -m pytest -q scientific-benchmark-case-builder-portable/tests/test_discovery_quality.py`

Expected: FAIL because the portable taxonomy/classifier and Discovery mode are absent.

- [ ] **Step 3: Port and harden the classifier**

Start from the installed Claude Code prototype, but classify structured evidence before error-string heuristics. Enforce priority:

```text
SOURCE_BLOCKED -> INFRA_INVALID -> CASE_DESIGN_BLOCKED
-> RUNTIME_BLOCKED -> RESOURCE_BLOCKED -> AGENT_LIMITATION
```

Malformed records exit 2; every valid record returns one decision.

- [ ] **Step 4: Run GREEN tests**

Run the focused test file and expect PASS.

- [ ] **Step 5: Commit**

```bash
git add scientific-benchmark-case-builder-portable/tests/test_discovery_quality.py \
  scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/failure-taxonomy.yaml \
  scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/classify_failure.py
git commit -m "feat(skill): add Discovery failure classification"
```

### Task 2: RED tests and implementation for cross-layer MLP draft consistency

**Files:**
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/categories/mlp/check_draft_consistency.py`
- Create: `scientific-benchmark-case-builder-portable/tests/fixtures/draft-consistency/good/`
- Create: `scientific-benchmark-case-builder-portable/tests/fixtures/draft-consistency/stale-hidden/`
- Create: `scientific-benchmark-case-builder-portable/tests/fixtures/draft-consistency/recipe-extra-system/`
- Modify: `scientific-benchmark-case-builder-portable/tests/test_discovery_quality.py`

**Interfaces:**
- CLI: `check_draft_consistency.py CASE_DIR --json`.
- Output: `{valid, errors, canonical_systems, observed_scopes, hash_mismatches}`.
- Authority: `public/system.json` top-level `interfaces` mapping.

- [ ] **Step 1: Write failing fixture tests**

The good fixture contains two generic systems (`surface-a`, `surface-b`) and matching hidden directories/manifests/recipe paths. Negative fixtures assert rejection when:

```text
hidden has stale surface-c
public recipe names unpublished surface-c
source lock hashes do not match
hidden manifest counts/systems disagree with bytes/directories
```

- [ ] **Step 2: Verify RED**

Run the focused tests; expect import/file-not-found failures for the missing checker.

- [ ] **Step 3: Implement the read-only checker**

Implement helpers:

```python
load_canonical_systems(case_dir) -> set[str]
hidden_directory_scopes(case_dir) -> dict[str, set[str]]
manifest_scope(path) -> set[str]
recipe_scope(public_dir, known_systems) -> set[str]
check_hash_lock(case_dir, lock_path) -> list[str]
check_case(case_dir) -> dict
```

Check both `reference/hidden-validation` and `tests/hidden` when present. Parse recipe systems from paths containing known system-like names and reject explicit dataset paths not in the canonical set. Hash-lock entries are path-relative to the case root.

- [ ] **Step 4: Verify GREEN and negative discrimination**

Run focused tests; good fixture PASS, both negatives FAIL with path-specific messages.

- [ ] **Step 5: Commit**

Commit checker, fixtures, and tests as one independently reviewable unit.

### Task 3: Add generic Prompt and cross-layer policies

**Files:**
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/discovery-and-refinement.md`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/cross-layer-consistency.md`
- Create: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/prompt-contract.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/lifecycle-and-gates.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/case-standard.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/common/reference-and-solution-policy.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/references/categories/mlp/readiness-policy.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`
- Test: `scientific-benchmark-case-builder-portable/tests/test_discovery_quality.py`

**Interfaces:**
- `SKILL.md` routes `discovery` to Common policy and the classifier.
- MLP design/construct/validate modes load `prompt-contract.md` and invoke `check_draft_consistency.py` before declaring Runnable Draft readiness.

- [ ] **Step 1: Add structural tests for required routed resources**

Assert the skill routes to the new references/scripts and the policy contains semantic slots for source relationship, full system label coverage, predeclared AL stopping, grouped holdout, published-artifact usage, initial-vs-labeled data, executed workflow, final manifest, and provenance.

- [ ] **Step 2: Verify RED**

Expected: FAIL because references and routes do not exist.

- [ ] **Step 3: Write concise policy references**

State positive contracts rather than copying the 042 narrative. `reference-and-solution-policy.md` must say partial/deferred assets cannot claim completed or invent evidence; preflight imports production code; scheduler completion is terminal-state based.

- [ ] **Step 4: Update lifecycle semantics**

Add `runnable_draft`, `discovery_complete`, and `promoted` as pre-reference lifecycle states while preserving release G0-G12 semantics. Distinguish Discovery prerequisites from post-PROMOTE release gates.

- [ ] **Step 5: Verify focused tests and word economy**

Run focused tests and inspect `SKILL.md` description/body for duplication.

- [ ] **Step 6: Commit**

Commit routed policy resources and entrypoint changes.

### Task 4: Integrate quality checks into validation and templates

**Files:**
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/validate_case.py`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/assets/case-template/common/case-design.yaml`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/assets/case-template/common/instruction.md`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/assets/case-template/common/VALIDATION.json`
- Modify: `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/assets/case-template/common/CONTRACT.md`
- Modify: `scientific-benchmark-case-builder-portable/tests/test_common_builder.py`
- Modify: `scientific-benchmark-case-builder-portable/tests/test_mlp_category.py`

**Interfaces:**
- `validate_case.py CASE --json` includes `quality_checks` and calls the category checker when category is MLP and a public system contract exists.
- Templates distinguish `paper_faithful` from `benchmark_adaptation` and use `planned`/`deferred` states rather than fabricated completion.

- [ ] **Step 1: Write RED integration tests**

Assert a scaffolded MLP case with coherent fixture passes, and an injected stale hidden system or stale hash fails `validate_case.py` with `quality_checks` evidence.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement optional category validation**

Keep generic/dummy future categories working. Only invoke the MLP checker when `category: mlp` and `public/system.json` exists; absence remains a precise readiness finding rather than a crash.

- [ ] **Step 4: Update templates**

Template instruction contains slots, not case-specific prose. `VALIDATION.json` separates Discovery prerequisites, Discovery decision, and post-PROMOTE release gates.

- [ ] **Step 5: Run focused and full portable tests**

Expected: all prior tests plus new tests PASS.

- [ ] **Step 6: Commit**

Commit integration and template changes.

### Task 5: Package version, manifest, hashes, and complete verification

**Files:**
- Modify: `scientific-benchmark-case-builder-portable/README.md`
- Modify: `scientific-benchmark-case-builder-portable/install.sh`
- Modify: `scientific-benchmark-case-builder-portable/manifest.json`
- Modify: `scientific-benchmark-case-builder-portable/SHA256SUMS`
- Modify: `scientific-benchmark-case-builder-portable/tests/test_portable_package.py`

**Interfaces:**
- Package version becomes `2.1.0`.
- `install.sh --check` covers every non-cache package file and validates hashes.

- [ ] **Step 1: Add RED package-content expectations**

Assert new references/scripts appear in the package and version is `2.1.0`.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Update README/install/manifest and regenerate SHA256SUMS**

Use a deterministic sorted file list excluding `SHA256SUMS`, `__pycache__`, and `*.pyc`.

- [ ] **Step 4: Run complete verification**

```bash
/Users/chenxuanjie/案例测试/dftworld2/.venv/bin/python -m pytest -q scientific-benchmark-case-builder-portable/tests
bash scientific-benchmark-case-builder-portable/install.sh --check
bash -n scientific-benchmark-case-builder-portable/install.sh
git diff --check
```

- [ ] **Step 5: Commit**

Commit package metadata and hashes.

### Task 6: Remove the Claude Code installed copy

**Targets:**
- Remove only: `/Users/chenxuanjie/.claude/skills/build-scientific-benchmark-case`
- Preserve: repository portable source and every other Claude/Codex skill.

- [ ] **Step 1: Resolve and verify the exact target**

Confirm it is a directory, not a symlink, and its `SKILL.md` name is exactly `build-scientific-benchmark-case`.

- [ ] **Step 2: Move to recoverable Trash location**

Move the directory to a uniquely named path under `~/.Trash/`, rather than recursively deleting it. This requires filesystem approval outside the workspace.

- [ ] **Step 3: Verify uninstall scope**

Assert the original target is absent, the trashed copy exists, portable source exists, and no other skill directory changed.

- [ ] **Step 4: Record memory and handoff**

Record portable version/commit and the recoverable uninstall location.
