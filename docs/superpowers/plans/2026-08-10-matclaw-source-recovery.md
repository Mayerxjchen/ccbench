# MatClaw CIPS Source Recovery Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable MatClaw CIPS source package that pins upstream papers and repository workspaces, verifies structures and the Task 3 field protocol, and truthfully records whether the teacher model and raw trajectories were recovered.

**Architecture:** A small Python module owns immutable source constants, hashing, inventory validation, and gate derivation. Raw upstream files remain separate from normalized common/task views. Tests exercise temporary inventories first, then validate the checked-in package without network access.

**Tech Stack:** Python 3.12, pytest, hashlib/json/pathlib, git, Poppler, pymatgen/ASE, and DeePMD-kit when a model is recovered.

## Global Constraints

- Do not create `031-*`, `032-*`, `033-*`, a MatClaw Dockerfile, or a base image.
- Paper source is arXiv `2604.02688v3`.
- Repository sources are `public@cebcf2be839af87663c0e5b64efaf1afe99f2e39` and `release@52557c077f5e3be8444a3f03ea10a647fbd442ca`.
- Provenance values distinguish repository, paper, author dataset, reconstruction, and missing upstream.
- A remote HPC path or job UUID is not a recovered file.
- Construction is unblocked only after an authoritative teacher model is hashed, type-map checked, and load-tested.
- Preserve unrelated worktree changes.

---

### Task 1: Inventory and Gate Engine

**Files:**
- Create: `benchmark/sources/matclaw/__init__.py`
- Create: `benchmark/sources/matclaw/recovery.py`
- Create: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Produces: `sha256_file(path: Path) -> str`
- Produces: `validate_inventory(root: Path, inventory: dict) -> list[str]`
- Produces: `derive_gate(inventory: dict) -> dict[str, bool]`

- [ ] **Step 1: Write failing tests for hashes, provenance, and gate derivation**

```python
def test_remote_path_does_not_recover_teacher():
    inventory = {"artifacts": [{
        "id": "teacher_model",
        "status": "missing",
        "provenance": "missing_upstream",
        "source_locator": "/pscratch/example/frozen_model.pb",
    }]}
    gate = derive_gate(inventory)
    assert gate["teacher_model_recovered"] is False
    assert gate["benchmark_construction_unblocked"] is False
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: collection fails because `benchmark.sources.matclaw.recovery` is absent.

- [ ] **Step 3: Implement minimal validation and gate derivation**

Reject unknown provenance, absent hashes for recovered files, hash/size mismatch, paths outside the source root, and recovered status backed only by a remote locator.

- [ ] **Step 4: Verify GREEN and commit**

```bash
uv run pytest tests/test_matclaw_source_recovery.py -q
git add benchmark/sources/matclaw/__init__.py benchmark/sources/matclaw/recovery.py tests/test_matclaw_source_recovery.py
git commit -m "feat: add MatClaw source recovery gate"
```

### Task 2: Pin and Recover MatClaw Sources

**Files:**
- Create: `benchmark/sources/matclaw/recover_sources.py`
- Create: `benchmark/sources/matclaw/source.lock.json`
- Create: `benchmark/sources/matclaw/paper/matclaw-2604.02688v3.pdf`
- Create: `benchmark/sources/matclaw/repository/release/`
- Modify: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Produces: `SOURCE_SPEC` with exact URLs, branches, commits, and selected paths.
- Produces: `recover_repository(source_repo: Path, destination: Path) -> list[Path]`.

- [ ] **Step 1: Add failing tests for immutable constants and selection boundaries**

Require the exact paper and commits, all five demonstration workspaces, and exclusion of unrelated `corpus/`, QA benchmarks, and application code.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: `SOURCE_SPEC` is absent.

- [ ] **Step 3: Implement pinned recovery**

Copy only the `.ref` CIPS inputs and `workspace_demo*` directories from a detached clone at the release commit. Download `https://arxiv.org/pdf/2604.02688v3` byte-for-byte.

- [ ] **Step 4: Recover and verify**

```bash
uv run python benchmark/sources/matclaw/recover_sources.py --repo-url https://github.com/cz2014/MatClaw.git --output benchmark/sources/matclaw
pdfinfo benchmark/sources/matclaw/paper/matclaw-2604.02688v3.pdf
uv run pytest tests/test_matclaw_source_recovery.py -q
```

Expected: exact commits reported, PDF readable, selected sources present, tests pass.

- [ ] **Step 5: Commit**

```bash
git add benchmark/sources/matclaw tests/test_matclaw_source_recovery.py
git commit -m "data: recover pinned MatClaw release sources"
```

### Task 3: Normalize and Audit CIPS Structures

**Files:**
- Create: `benchmark/sources/matclaw/common/CuInP2S6.cif`
- Create: `benchmark/sources/matclaw/common/cips_monolayer.cif`
- Create: `benchmark/sources/matclaw/common/cips_monolayer.data`
- Create: `benchmark/sources/matclaw/common/structure-audit.json`
- Modify: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Consumes: pinned `.ref` structures.
- Produces: formula, atom count, lattice, periodicity, source path, and SHA-256.

- [ ] **Step 1: Add failing structure tests**

Parse both CIFs with pymatgen, require CIPS stoichiometry, require 10 atoms for `CuInP2S6.cif`, and require byte identity with upstream sources.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: common files are absent.

- [ ] **Step 3: Copy without transformation and generate audit**

Preserve source bytes. Parse with pymatgen and ASE where supported; record parser disagreement as a blocker.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_matclaw_source_recovery.py -q
git add benchmark/sources/matclaw/common tests/test_matclaw_source_recovery.py
git commit -m "data: audit MatClaw CIPS structures"
```

### Task 4: Recover Task Views and Electric-Field Protocol

**Files:**
- Create: `benchmark/sources/matclaw/task1/manifest.json`
- Create: `benchmark/sources/matclaw/task2/manifest.json`
- Create: `benchmark/sources/matclaw/task3/manifest.json`
- Create: `benchmark/sources/matclaw/task3/electric-field-protocol.json`
- Modify: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Consumes: pinned histories, reports, CSV, JSON, figures, and training data.
- Produces: task-view manifests pointing to raw snapshot hashes.
- Produces: protocol equations, charges, units, and exact history evidence.

- [ ] **Step 1: Add failing coverage and protocol tests**

Require Task 1a/1b, Task 2a/2b, Task 3, `SumCalculator([DeePMD, UniformElectricForce])`, `F_DP + qE`, external energy, Cu `0.765`, In/P/S `-0.085`, and history citations.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: task manifests are absent.

- [ ] **Step 3: Generate manifests and evidence**

Do not duplicate large workspaces into task views. Keep the complete upstream histories in the raw snapshot.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_matclaw_source_recovery.py -q
git add benchmark/sources/matclaw/task1 benchmark/sources/matclaw/task2 benchmark/sources/matclaw/task3 tests/test_matclaw_source_recovery.py
git commit -m "data: recover MatClaw task provenance"
```

### Task 5: Recover and Validate the Teacher Model

**Files:**
- Create: `benchmark/sources/matclaw/paper/he-physrevb-108-024305.pdf`
- Create when available: files under `benchmark/sources/matclaw/common/teacher-model/` retaining their upstream filenames
- Create: `benchmark/sources/matclaw/common/teacher-model/recovery.json`
- Modify: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Consumes: He et al. DOI metadata and its cited AIS Square record.
- Produces: a hashed/load-tested model or a structured missing-upstream record.

- [ ] **Step 1: Add failing honest-status tests**

If a model exists, require hash, type map `Cu In P S`, authoritative locator, and successful load evidence. If absent, require `missing_upstream` and forbid a true model gate.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: `recovery.json` is absent.

- [ ] **Step 3: Follow only authoritative author-data links**

Record AIS Square redirects, record IDs, licenses, filenames, hashes, and observed access failures. Do not substitute an unrelated CIPS model.

- [ ] **Step 4: Load-test or record blocker**

When present, inspect metadata and evaluate the 10-atom structure in a compatible DeePMD environment. Otherwise preserve precise evidence and keep the gate false.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_matclaw_source_recovery.py -q
git add benchmark/sources/matclaw/common/teacher-model benchmark/sources/matclaw/paper tests/test_matclaw_source_recovery.py
git commit -m "data: record MatClaw teacher model recovery"
```

### Task 6: Final Inventory, Gate, and Documentation

**Files:**
- Create: `benchmark/sources/matclaw/inventory.json`
- Create: `benchmark/sources/matclaw/recovery_gate.json`
- Create: `benchmark/sources/matclaw/README.md`
- Modify: `tests/test_matclaw_source_recovery.py`

**Interfaces:**
- Consumes: recovered files and blocker records.
- Produces: canonical inventory and derived gate.

- [ ] **Step 1: Add failing end-to-end package tests**

Require all hashes to validate, checked-in gate to equal `derive_gate`, README status to agree, and no forbidden case/image files to exist.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_matclaw_source_recovery.py -q`

Expected: final metadata is absent.

- [ ] **Step 3: Generate final metadata**

```bash
uv run python benchmark/sources/matclaw/recovery.py --root benchmark/sources/matclaw --write
```

- [ ] **Step 4: Run full verification**

```bash
uv run pytest tests/test_matclaw_source_recovery.py -q
uv run pytest tests/ -q
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, and no 031-033 directory or Dockerfile exists.

- [ ] **Step 5: Commit**

```bash
git add benchmark/sources/matclaw tests/test_matclaw_source_recovery.py
git commit -m "docs: finalize MatClaw source recovery audit"
```
