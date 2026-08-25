# Local Workspace Organization Implementation Plan

> **Superseded on 2026-08-25:** Do not execute this migration plan. The approved replacement is `docs/superpowers/plans/2026-08-25-workspace-consolidation.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `/Users/chenxuanjie/案例测试` into explicit source, worktree, Trusted evidence, runtime-state, independent-project, external, document, and archive areas without changing DFTWorld Case paths or losing testability.

**Architecture:** Keep `dftworld2` and its 001–042 directories fixed. Version a workspace manifest/schema/checker inside DFTWorld, generate root navigation files, and migrate one ownership category at a time with hash verification and compatibility links. Deletion remains owned by `2026-08-23-local-workspace-cleanup.md`.

**Tech Stack:** Python 3.12+, `tomllib`, JSON Schema 2020-12, Git worktrees, SHA-256, tar.zst, pytest, unittest, macOS filesystem tools.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-08-23-local-workspace-layout-design.md`.
- `/Users/chenxuanjie/案例测试/dftworld2` remains at its current absolute path.
- DFTWorld Case directories `001-*` through `042-*` remain at repository root.
- The workspace root is not initialized as a Git repository.
- No move and deletion occur in the same step.
- Every durable/trusted move is copy-or-move, rehash, read-back, receipt, then compatibility link.
- `git clean -fdX`, `git reset --hard`, broad recursive deletion, and direct worktree directory moves are forbidden.
- Formal evidence and hidden fixtures never enter Candidate-visible or external project directories.
- Existing immutable manifests are not rewritten; relocation is expressed by new receipts and compatibility paths.
- Current ElectroMind Skills baseline is explicitly unhealthy: 10 tests, 3 PASS, 1 FAIL, 6 ERROR. Organization must preserve and report this state, not call it GREEN.
- Every task has a rollback path and ends with a scoped verification.

---

## File and Directory Map

**Versioned in DFTWorld:**

- Create: `schemas/local-workspace.schema.json`
- Create: `infra/workspace/workspace.example.toml`
- Create: `scripts/workspace_layout.py`
- Create: `tests/workspace/test_workspace_layout.py`
- Create: `docs/workspace/README.md`

**Generated or migrated outside Git:**

- Create: `/Users/chenxuanjie/案例测试/README.md`
- Create: `/Users/chenxuanjie/案例测试/workspace.toml`
- Create: `/Users/chenxuanjie/案例测试/worktrees/dftworld2/`
- Create: `/Users/chenxuanjie/案例测试/trusted-evidence/`
- Create: `/Users/chenxuanjie/案例测试/workspace-state/`
- Create: `/Users/chenxuanjie/案例测试/projects/electromind-skills/`
- Create: `/Users/chenxuanjie/案例测试/external/`
- Create: `/Users/chenxuanjie/案例测试/documents/`
- Create: `/Users/chenxuanjie/案例测试/archives/git-bundles/`

---

### Task 1: Freeze the Pre-Move Inventory

**Files:**
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/layout-before.json`
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/layout-before-sha256.txt`

- [ ] **Step 1: Record root ownership inventory**

Record every top-level path, type, byte count, inode/device, symlink target,
Git root, and modification time. Expected top-level durable inputs are
`dftworld2`, `hpc-cleanup-20260822-archive`, `local-cleanup-20260823`, `skills`,
`tests`, and the DOCX document.

- [ ] **Step 2: Record Git identities**

Record DFTWorld HEAD `21ab5e3` or its current descendant, `git status --short`,
all worktrees, and the nested `dftworld2/ai2kit` Git HEAD/status/remotes without
printing credential-bearing URLs.

- [ ] **Step 3: Hash durable inputs**

Hash every Trusted tar.zst, cleanup receipt, Git bundle, dirty patch, and the
DOCX. Do not recursively hash `.venv`, cache, or temporary state.

- [ ] **Step 4: Record test baselines**

Run:

```bash
cd /Users/chenxuanjie/案例测试/dftworld2
.venv/bin/pytest -q -p no:cacheprovider tests

cd /Users/chenxuanjie/案例测试
python -m unittest discover -s tests -v
```

Expected at plan authoring: DFTWorld reports 1201 PASS, 0 FAIL, 9 SKIP;
ElectroMind Skills reports exactly 3 PASS, 1 FAIL, 6 ERROR. Any different
result stops migration and requires updating the sealed baseline first.

- [ ] **Step 5: Stop on ambiguity**

Stop if a durable path is a symlink to an unknown target, a nested repository
is dirty without a bundle, or the archive digest differs from its receipt.

---

### Task 2: Add the Workspace Manifest and Checker

**Files:**
- Create: `schemas/local-workspace.schema.json`
- Create: `infra/workspace/workspace.example.toml`
- Create: `scripts/workspace_layout.py`
- Create: `tests/workspace/test_workspace_layout.py`
- Create: `docs/workspace/README.md`

**Interfaces:**

- `load_manifest(path: Path) -> dict`
- `validate_manifest(payload: dict) -> list[str]`
- `check_layout(root: Path, payload: dict) -> list[Finding]`
- `initialize_root(root: Path, payload: dict) -> None`
- `run_project(root: Path, project_name: str, payload: dict) -> int`

- [ ] **Step 1: Write RED schema and ownership tests**

Tests reject absolute category paths, duplicate ownership, a project under
Trusted evidence, a runtime-state path inside DFTWorld, an unknown retention
class, and worktree paths outside `worktrees/dftworld2`.

```python
def test_runtime_state_cannot_live_inside_repository(valid_manifest):
    valid_manifest["paths"]["state"] = "dftworld2/tmp"
    assert "state path overlaps repository" in validate_manifest(valid_manifest)

def test_trusted_path_cannot_be_candidate_project(valid_manifest):
    valid_manifest["projects"][0]["path"] = "trusted-evidence/hpc"
    assert "project path overlaps trusted evidence" in validate_manifest(valid_manifest)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/pytest -q tests/workspace/test_workspace_layout.py
```

Expected: import failure for `scripts.workspace_layout`.

- [ ] **Step 3: Define the manifest schema**

The exact root manifest contains:

```toml
schema_version = 1
workspace_id = "scientific-benchmark-workspace"

[paths]
benchmark = "dftworld2"
worktrees = "worktrees/dftworld2"
trusted = "trusted-evidence"
state = "workspace-state"
projects = "projects"
external = "external"
documents = "documents"
archives = "archives"

[[projects]]
name = "dftworld2"
path = "dftworld2"
kind = "git"
retention = "durable"
enabled = true
test_command = [".venv/bin/pytest", "-q", "-p", "no:cacheprovider", "tests"]

[[projects]]
name = "electromind-skills"
path = "projects/electromind-skills"
kind = "directory"
retention = "durable"
enabled = false
blocked_reason = "fixture and route baseline is not green"
test_command = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
```

- [ ] **Step 4: Implement fail-closed checker**

The checker resolves paths without following unknown links, detects category
overlap, verifies Git worktree registration, reports legacy compatibility
links, and never mutates during `check`.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/workspace/test_workspace_layout.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add schemas/local-workspace.schema.json infra/workspace/workspace.example.toml \
  scripts/workspace_layout.py tests/workspace/test_workspace_layout.py \
  docs/workspace/README.md
git commit -m "feat(workspace): add local layout contract and checker"
```

---

### Task 3: Initialize the Root Categories and Navigation

**Files:**
- Create: `/Users/chenxuanjie/案例测试/README.md`
- Create: `/Users/chenxuanjie/案例测试/workspace.toml`
- Create category directories from the approved design.

- [ ] **Step 1: Dry-run initialization**

Run:

```bash
cd /Users/chenxuanjie/案例测试/dftworld2
.venv/bin/python scripts/workspace_layout.py init \
  --root /Users/chenxuanjie/案例测试 --dry-run
```

Expected: only the approved root README, manifest, and empty category
directories are listed; `dftworld2` is unchanged.

- [ ] **Step 2: Initialize categories**

Create `worktrees/dftworld2`, `trusted-evidence/hpc/2026-08-22`,
`trusted-evidence/local-cleanup/2026-08-23`, `workspace-state/runs`,
`workspace-state/tmp`, `workspace-state/cache`, `workspace-state/venvs`,
`projects`, `external`, `documents`, and `archives/git-bundles`.

- [ ] **Step 3: Write root README**

The README defines each category, its retention class, the command to check
layout, the command to test projects, and the rule that 001–042 stay inside
DFTWorld.

- [ ] **Step 4: Check layout**

Run:

```bash
cd /Users/chenxuanjie/案例测试/dftworld2
.venv/bin/python scripts/workspace_layout.py check \
  --root /Users/chenxuanjie/案例测试
```

Expected: legacy paths reported as `PENDING_MIGRATION`, no ownership conflict.

---

### Task 4: Migrate Trusted HPC Evidence

**Source:** `/Users/chenxuanjie/案例测试/hpc-cleanup-20260822-archive`

**Destinations:**

- tar.zst files: `trusted-evidence/hpc/2026-08-22/archives/`
- rehash/file manifests and markers: `trusted-evidence/hpc/2026-08-22/manifests/`
- relocation receipts: `trusted-evidence/hpc/2026-08-22/relocation-receipts/`

- [ ] **Step 1: Finish cleanup-plan extraction removal**

Execute Task 2 of `2026-08-23-local-workspace-cleanup.md` first. Only compressed
archives, manifests, receipts, and verification markers may remain at source.

- [ ] **Step 2: Copy into categorized destination**

Copy each file without altering bytes or timestamps. Record old path, new path,
size, old SHA-256, and new SHA-256 in
`relocation-receipts/local-layout-migration.json`.

- [ ] **Step 3: Verify destination and read-back**

Recompute all archive hashes and fresh-extract the smallest gate archive into
`workspace-state/tmp/archive-readback`. Confirm its file manifest, then remove
only that disposable read-back directory.

- [ ] **Step 4: Build compatibility directory**

After every destination file verifies, replace the old archive directory with
a compatibility directory containing relative symlinks for every former file.
Verify that hashing through every old path produces the original digest.

- [ ] **Step 5: Check Candidate exclusion**

Run Candidate bundle audit and assert neither canonical Trusted paths nor the
legacy compatibility directory enters a bundle.

Expected: approximately 2.9 GB of durable archives at one canonical location,
with old-path compatibility and no duplicate archive bytes.

---

### Task 5: Classify Local Cleanup Records and Git Bundles

**Source:** `/Users/chenxuanjie/案例测试/local-cleanup-20260823`

**Destinations:**

- `legacy-branches-20260823.bundle` -> `archives/git-bundles/`
- receipts, dirty patch, SHA lists, metadata archive ->
  `trusted-evidence/local-cleanup/2026-08-23/`

- [ ] **Step 1: Verify the Git bundle**

Run `git bundle verify` and record branch tip SHAs plus bundle SHA-256.

- [ ] **Step 2: Verify cleanup records**

Parse `receipt.json`, verify `jobs-metadata.tar.zst`, and run
`git apply --check` against a temporary checkout for `main-dirty.patch`.

- [ ] **Step 3: Copy and rehash by category**

Copy the bundle and Trusted cleanup records to their exact destinations. Do
not place the bundle under Trusted evidence because it is source-history
archive, not hidden scientific evidence.

- [ ] **Step 4: Add compatibility links**

Replace the old local-cleanup directory with a small compatibility directory
containing relative symlinks to the new canonical files. Verify all hashes
through both locations.

---

### Task 6: Move the ElectroMind Skills Project as One Unit

**Sources:**

- `/Users/chenxuanjie/案例测试/skills`
- `/Users/chenxuanjie/案例测试/tests`

**Destination:** `/Users/chenxuanjie/案例测试/projects/electromind-skills`

- [ ] **Step 1: Seal the known failing baseline**

Record the exact 10-test result: 3 PASS, 1 FAIL, 6 ERROR. Record missing fixture
and missing route findings separately from filesystem migration.

- [ ] **Step 2: Copy both roots together**

Create the destination and copy `skills/` and `tests/` without editing content.
Rehash all non-pyc files and compare source/destination manifests.

- [ ] **Step 3: Run tests from destination**

Run:

```bash
cd /Users/chenxuanjie/案例测试/projects/electromind-skills
python -m unittest discover -s tests -v
```

Expected: the same 3 PASS, 1 FAIL, 6 ERROR classification. Any different test
is a migration regression and triggers rollback.

- [ ] **Step 4: Remove source copies only after parity**

After byte and test parity, remove the two old root copies. Do not create legacy
symlinks because the project tests resolve their root relative to their new
location and no external runtime should depend on the old authoring path.

- [ ] **Step 5: Keep project disabled in workspace manifest**

`test --all` reports `BLOCKED_PROJECT` for ElectroMind Skills until its missing
fixtures and route integration are repaired in a separate project.

---

### Task 7: Move the Nested ai2kit Repository to External Ownership

**Source:** `/Users/chenxuanjie/案例测试/dftworld2/ai2kit`

**Destination:** `/Users/chenxuanjie/案例测试/external/ai2kit`

- [ ] **Step 1: Seal nested Git state**

Record sanitized remote identity, HEAD, branches, status, ignored files, and a
Git bundle if any commit is not reachable from its configured remote. If the
repository is dirty, preserve a binary patch and untracked manifest first.

- [ ] **Step 2: Prove DFTWorld has no runtime dependency on the local path**

Classify `rg ai2kit/` results. Historical documentation and frozen provenance
are allowed; executable code importing or opening `dftworld2/ai2kit` blocks the
move until updated.

- [ ] **Step 3: Move repository without altering `.git`**

Move the complete nested repository to `external/ai2kit`, then verify HEAD,
status, remotes, and file manifest.

- [ ] **Step 4: Create compatibility symlink**

Create `dftworld2/ai2kit` as a relative symlink to `../external/ai2kit` for one
migration cycle. Confirm DFTWorld Git continues to ignore it and Candidate
bundle audit excludes it.

- [ ] **Step 5: Update workspace manifest**

Register `ai2kit` as `kind = "git"`, `retention = "external"`, and do not include
it in DFTWorld's default test suite.

---

### Task 8: Move Disposable State Out of DFTWorld

**Sources:**

- `dftworld2/tmp/`
- `dftworld2/.venv/`
- future DFTWorld run directories

**Destinations:**

- `workspace-state/tmp/dftworld2-legacy-20260823/`
- `workspace-state/venvs/dftworld2-main/`
- `workspace-state/runs/dftworld2/`

- [ ] **Step 1: Inventory mixed tmp**

Record every top-level tmp child and preserve the directory byte-for-byte. This
task relocates tmp; it does not decide which unrelated user projects to delete.

- [ ] **Step 2: Move tmp and add compatibility link**

Move the complete tmp tree to its dated state directory and create a relative
`dftworld2/tmp` compatibility symlink. Run source scans and relevant tests.

- [ ] **Step 3: Move the virtual environment with rollback**

Record `.venv/bin/python` version and installed-package fingerprint. Move the
environment to `workspace-state/venvs/dftworld2-main`, create a `.venv`
compatibility symlink, and run Python import plus scoped pytest checks. Restore
the original directory immediately if executable shebangs or `sys.prefix`
break.

- [ ] **Step 4: Configure future runs**

Root navigation documents require all manual/eval commands to pass the
workspace-state run path through the existing `--jobs-dir`/run-root interface.
No Case, Skill, or committed release may encode the absolute local state root.

- [ ] **Step 5: Verify source cleanliness**

Assert the repository contains no untracked run workspace and layout check
reports state only under `workspace-state`.

---

### Task 9: Move Documents and Establish Future Worktree Policy

**Moves:**

- DOCX -> `documents/`
- future Git bundles -> `archives/git-bundles/`
- future linked worktrees -> `worktrees/dftworld2/`

- [ ] **Step 1: Move and hash the document**

Move `计算化学案例构造与评测_031-034增补版.docx` into `documents/` and
verify its SHA-256.

- [ ] **Step 2: Document worktree commands**

The root README uses the concrete example:

```bash
git -C /Users/chenxuanjie/案例测试/dftworld2 worktree add \
  /Users/chenxuanjie/案例测试/worktrees/dftworld2/feature-example \
  -b feature/example
```

Moving/removing uses `git worktree move/remove`; direct filesystem moves are
forbidden.

- [ ] **Step 3: Remove root cosmetic caches**

Remove only root `.DS_Store` and root `.pytest_cache` after the Skills project
move. Do not run a recursive ignored-file clean.

- [ ] **Step 4: Check root allowlist**

Expected root entries are README, workspace manifest, DFTWorld, worktrees,
Trusted evidence, workspace state, projects, external, documents, and archives.

---

### Task 10: Unified Layout and Test Acceptance

**Files:**
- Modify: `scripts/workspace_layout.py`
- Modify: `tests/workspace/test_workspace_layout.py`
- Modify: `docs/workspace/README.md`

- [ ] **Step 1: Add migration acceptance tests**

Test canonical paths, compatibility links, archive hash lookup, nested Git
identity, active worktree registration, state exclusion, and blocked-project
reporting.

- [ ] **Step 2: Run layout check**

Run:

```bash
cd /Users/chenxuanjie/案例测试/dftworld2
.venv/bin/python scripts/workspace_layout.py check \
  --root /Users/chenxuanjie/案例测试 --json
```

Expected: `valid=true`, no ownership conflicts, ElectroMind Skills explicitly
blocked rather than silently skipped.

- [ ] **Step 3: Run DFTWorld and portable tests**

Run DFTWorld full tests outside the socket-restricted sandbox and the portable
Skill suites. Expected at plan authoring: DFTWorld 1201 PASS, 0 FAIL, 9 SKIP;
portable package 95 PASS. Any deliberately changed baseline must be sealed in
Task 1 before the first move.

- [ ] **Step 4: Run workspace test command**

Run:

```bash
.venv/bin/python scripts/workspace_layout.py test \
  --root /Users/chenxuanjie/案例测试 --all --json
```

Expected: DFTWorld PASS; ElectroMind Skills `BLOCKED_PROJECT` with its recorded
reason; no test writes outside `workspace-state/runs/tests`.

- [ ] **Step 5: Record final receipt**

Write before/after tree, moved-path map, hashes, compatibility links, test
results, and rollback locations to
`trusted-evidence/local-cleanup/2026-08-23/layout-migration-receipt.json`.

- [ ] **Step 6: Commit versioned tooling**

```bash
git add scripts/workspace_layout.py tests/workspace/test_workspace_layout.py \
  docs/workspace/README.md schemas/local-workspace.schema.json \
  infra/workspace/workspace.example.toml
git commit -m "test(workspace): verify organized local layout"
```

---

## Adversarial Checklist

| Risk | Required defense |
|---|---|
| Moving DFTWorld changes Case/release paths | Repository and 001–042 paths fixed |
| Trusted archive move breaks old receipt path | Categorized copy, second receipt, old-path symlink map |
| `git clean -fdX` deletes hidden evidence | Command explicitly forbidden; allowlist deletion only |
| Worktree moved outside Git | Only `git worktree move/remove` accepted |
| ai2kit nested Git history lost | HEAD/status/bundle gate and whole-repo move |
| Skills migration hides existing failures | Exact before/after 3 PASS, 1 FAIL, 6 ERROR parity |
| Test writes into Case fixtures | Dedicated workspace-state test output root |
| Virtualenv move breaks shebangs | Compatibility symlink, import/test gate, immediate rollback |
| Root becomes accidental Git superproject | Root remains plain directory |
| Compatibility link exposes Trusted bytes | Candidate bundle/leak audit rejects both link and target |
| Mixed tmp data is deleted as cache | Relocation only; deletion belongs separate review |
| Unique branch disappears during organization | Verified Git bundle and separate branch-deletion authority |

## Completion Definition

- root entries match the approved category allowlist;
- DFTWorld and 001–042 paths are unchanged;
- Trusted evidence hashes verify at canonical and compatibility paths;
- DFTWorld contains no untracked external repository or run workspace;
- temporary state lives only under `workspace-state`;
- independent Skills are isolated and their unhealthy baseline is explicit;
- document and Git bundles have stable owners;
- layout checker reports valid;
- DFTWorld tests have no organization-induced regression;
- one command checks the workspace and runs every enabled project suite.
