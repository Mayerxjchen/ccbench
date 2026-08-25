# Workspace Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate `/Users/chenxuanjie/案例测试` around `dftworld2/`, retain all current 042, skill-ablation-v2, HPC cleanup, and dispatcher qualification work, and remove verified duplicate, superseded, reproducible, or unreferenced files and code.

**Architecture:** Treat `dftworld2/` as the only active Git worktree. Seal the current dirty state first, migrate qualification assets into versioned evidence/reference/script locations, make the remaining workspace ownership explicit, then delete one verified class at a time. Every destructive batch is gated by hashes, reference checks, scoped tests, and an append-only cleanup receipt.

**Tech Stack:** Git and Git worktrees, Python 3.12, pytest, Bash, SHA-256 (`shasum -a 256`), `tar.zst`, `rg`, `uv`, macOS filesystem tools.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-08-25-workspace-consolidation-design.md`.
- This plan supersedes the unexecuted layout migration in `docs/superpowers/plans/2026-08-23-local-workspace-organization.md`; it does not undo the completed cleanup recorded by `local-cleanup-20260823/receipt.json`.
- Preserve the exact current bytes of `042-go-water-dpmp/instruction.md`, `infra/runs/skill-ablation-v2.yaml`, `.cluster-agents.md`, `docs/experiments/`, `evidence/hpc-cleanup-20260822/`, and all qualification assets until they are committed or sealed.
- Do not use `git clean`, `git reset --hard`, `git checkout --`, broad recursive deletion, or wildcard deletion.
- Do not modify or delete `.env`; exclude it from every archive and verify that it remains ignored.
- Do not delete any path below `evidence/`, `reference/`, `solution/`, `tests/hidden/`, or `tests/fixtures/` merely because it is large or untracked.
- Do not submit, cancel, or delete remote HPC jobs or files. This plan changes local files only.
- Remove linked worktrees only with `git worktree remove`, followed by `git worktree prune`.
- A source is deleted only after the destination exists, hashes match, path references are resolved, and the relevant tests pass.
- Each deletion batch records exact paths, reason, bytes before/after, verification command, result, and recovery source in `evidence/local-cleanup-20260825/decision-ledger.json`.
- Stop the batch on a hash mismatch, an unknown symlink, a test regression, an unresolved current reference, or a file absent from the sealed inventory.
- Commit only task-owned paths. Existing user changes remain unstaged unless their migration task explicitly owns them.

---

## File and Directory Map

**Create in `dftworld2/`:**

- `evidence/local-cleanup-20260825/before-state.json`
- `evidence/local-cleanup-20260825/protected-files.sha256`
- `evidence/local-cleanup-20260825/decision-ledger.json`
- `evidence/local-cleanup-20260825/FINAL-REPORT.md`
- `evidence/hpc-dispatcher/qualification/site-v1/INDEX.md`
- `evidence/hpc-dispatcher/qualification/site-v1/SHA256SUMS`
- `evidence/hpc-dispatcher/qualification/site-v1/operator-scripts/original-run_qualify.sh`
- `evidence/hpc-dispatcher/qualification/site-v1/operator-scripts/original-supervise_qualify.sh`
- `reference/runtime/cp2k-runtime.lock.json`
- `reference/runtime/deepmd-jax-runtime.lock.json`
- `reference/runtime/README.md`
- `scripts/qualification/run_hpc_dispatcher.sh`
- `scripts/qualification/supervise_hpc_dispatcher.sh`
- `tests/hpc/test_qualification_operator_scripts.py`
- `docs/operations/workspace-maintenance.md`

**Modify in `dftworld2/`:**

- `.gitignore`
- `README.md`
- `tests/docs/test_architecture_contracts.py`
- `docs/superpowers/plans/2026-08-23-local-workspace-organization.md`

**Create or consolidate outside Git:**

- `/Users/chenxuanjie/案例测试/README.md`
- `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/INDEX.md`
- `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/RECOVERY.md`
- `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/SHA256SUMS`

**Remove only after their gates pass:**

- `/Users/chenxuanjie/案例测试/dftworld2-qualification/`
- `/Users/chenxuanjie/案例测试/dftworld2/.pytest_cache/`
- Python `__pycache__/` and `*.pyc` under the two active local projects
- `/Users/chenxuanjie/案例测试/dftworld2/tmp/`
- `/Users/chenxuanjie/案例测试/dftworld2/.venv/` during a verified clean rebuild; one active environment is recreated and retained
- `/Users/chenxuanjie/案例测试/worktrees/` when empty
- `/Users/chenxuanjie/案例测试/.claude/scheduled_tasks.lock` and its parent only when no process holds the lock and no scheduled-task definition uses it
- `/Users/chenxuanjie/案例测试/dpj_sif_pipeline.py` after its provenance is sealed and no caller remains
- `/Users/chenxuanjie/案例测试/dftworld2/ai2kit/` after the 034 lineage comparison passes
- obsolete large archive packages that pass Task 6's explicit decision gates

---

### Task 1: Seal the Current Workspace and Define the Protected Set

**Files:**

- Create: `evidence/local-cleanup-20260825/before-state.json`
- Create: `evidence/local-cleanup-20260825/protected-files.sha256`
- Create: `evidence/local-cleanup-20260825/decision-ledger.json`

**Interfaces:**

- Produces an immutable baseline consumed by every later deletion gate.
- `decision-ledger.json` is a JSON array; every entry has `path`, `classification`, `reason`, `bytes`, `verification`, `recovery`, and `status`.

- [ ] **Step 1: Record both Git worktrees and current changes**

Run from `/Users/chenxuanjie/案例测试/dftworld2`:

```bash
git status --short --branch
git diff --binary -- 042-go-water-dpmp/instruction.md infra/runs/skill-ablation-v2.yaml
git worktree list --porcelain
git -C /Users/chenxuanjie/案例测试/dftworld2-qualification status --short --branch
```

Expected: main contains the two known modified files plus current untracked work; qualification is detached at the same base commit and contains only the qualification evidence, locks, and runner scripts already inventoried in the design.

- [ ] **Step 2: Write the baseline JSON**

`before-state.json` records:

```json
{
  "schema": "local-workspace-cleanup/v1",
  "date": "2026-08-25",
  "workspace": "/Users/chenxuanjie/案例测试",
  "canonical_repo": "dftworld2",
  "protected_work": [
    "042-go-water-dpmp",
    "infra/runs/skill-ablation-v2.yaml",
    "docs/experiments",
    "evidence/hpc-cleanup-20260822",
    "evidence/hpc-dispatcher/qualification/site-v1",
    "reference/runtime"
  ],
  "forbidden_remote_mutation": true
}
```

Append the actual HEADs, statuses, top-level byte counts, and full qualification untracked-file list as structured fields; do not include `.env` contents or remote URLs with embedded credentials.

- [ ] **Step 3: Hash the protected bytes**

Generate `protected-files.sha256` for the two modified files, current experiment docs, HPC cleanup receipts/reports, qualification files, root Skills sources/tests, DOCX, `dpj_sif_pipeline.py`, and all `ai2kit/` non-Git files. Exclude `.env`, `.git/`, `.venv/`, caches, and `tmp/`.

Run:

```bash
shasum -a 256 -c evidence/local-cleanup-20260825/protected-files.sha256
```

Expected: every entry reports `OK`.

- [ ] **Step 4: Initialize the append-only decision ledger**

Write `[]` followed by a newline. Later tasks may append entries through a small one-shot formatter or `apply_patch`, but must never remove earlier entries.

- [ ] **Step 5: Capture the test baseline**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider 042-go-water-dpmp/tests
```

Expected: both commands exit 0. If the pre-cleanup baseline is not green, record the exact failing node IDs in `before-state.json` and require later runs to have no additional failures; do not claim the final workspace is fully green.

- [ ] **Step 6: Commit the baseline**

```bash
git add evidence/local-cleanup-20260825/before-state.json \
  evidence/local-cleanup-20260825/protected-files.sha256 \
  evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "chore(workspace): seal cleanup baseline"
```

---

### Task 2: Add Maintenance Documentation and Ignore Contracts

**Files:**

- Create: `docs/operations/workspace-maintenance.md`
- Create: `/Users/chenxuanjie/案例测试/README.md`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `tests/docs/test_architecture_contracts.py`
- Modify: `docs/superpowers/plans/2026-08-23-local-workspace-organization.md`

**Interfaces:**

- The operations document owns cache policy, worktree lifecycle, evidence retention, and archive decision rules.
- The root README maps each retained top-level item to one owner and retention class.

- [ ] **Step 1: Write failing documentation-contract tests**

Add these tests to `tests/docs/test_architecture_contracts.py`:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_workspace_maintenance_policy_names_protected_areas():
    text = (REPO_ROOT / "docs/operations/workspace-maintenance.md").read_text()
    for required in (
        "042-go-water-dpmp",
        "evidence/hpc-cleanup-20260822",
        "evidence/hpc-dispatcher",
        "git worktree remove",
        "decision-ledger.json",
    ):
        assert required in text


def test_gitignore_covers_only_rebuildable_workspace_state():
    lines = set((REPO_ROOT / ".gitignore").read_text().splitlines())
    assert {".venv", "__pycache__/", "*.pyc", ".pytest_cache/", "/tmp/"} <= lines
    assert "/evidence/" not in lines
    assert "/reference/" not in lines
```

- [ ] **Step 2: Run the tests to verify RED**

```bash
.venv/bin/pytest -q tests/docs/test_architecture_contracts.py
```

Expected: failure because `docs/operations/workspace-maintenance.md` does not exist and `/tmp/` is not yet ignored.

- [ ] **Step 3: Write the maintenance policy**

Document these exact retention classes:

- `source`: tracked code, cases, schemas, scripts, docs;
- `current-evidence`: 042, skill-ablation-v2, HPC cleanup, qualification attempts and runtime locks;
- `rebuildable`: venvs, caches, `tmp/`, empty workspaces;
- `recovery`: verified Git bundle, patch, manifest, and only the archive payloads still needed by current releases;
- `external-reference`: root Skills project and the 034 ai2kit lineage until its comparison gate closes.

Include commands for `git worktree list`, `shasum -a 256 -c`, cache cleanup, and the rule that a worktree directory is never removed directly.

- [ ] **Step 4: Update ignore rules and navigation**

Add exactly these missing ignore patterns:

```gitignore
.pytest_cache/
/tmp/
```

Keep `.env`, `.venv`, `__pycache__/`, `*.pyc`, `.worktrees/`, and `jobs/`. Do not ignore `evidence/`, `reference/`, runtime locks, receipts, manifests, or experiment YAML.

Add a short “Workspace maintenance” link to the repository README. Write the root README with one-line ownership for `dftworld2/`, `skills/`, `tests/`, `archives/`, and the DOCX.

- [ ] **Step 5: Mark the old unexecuted layout plan as superseded**

Immediately below its title, add:

```markdown
> **Superseded on 2026-08-25:** Do not execute this migration plan. The approved replacement is `docs/superpowers/plans/2026-08-25-workspace-consolidation.md`.
```

- [ ] **Step 6: Run tests and commit**

```bash
.venv/bin/pytest -q tests/docs/test_architecture_contracts.py
git diff --check
git add .gitignore README.md docs/operations/workspace-maintenance.md \
  tests/docs/test_architecture_contracts.py \
  docs/superpowers/plans/2026-08-23-local-workspace-organization.md
git commit -m "docs(workspace): define maintenance and retention rules"
```

Expected: tests pass; the root README remains outside Git and is recorded in the cleanup ledger.

---

### Task 3: Migrate Qualification Locks, Evidence, and Operator Scripts

**Files:**

- Create: `reference/runtime/cp2k-runtime.lock.json`
- Create: `reference/runtime/deepmd-jax-runtime.lock.json`
- Create: `reference/runtime/README.md`
- Create: `evidence/hpc-dispatcher/qualification/site-v1/INDEX.md`
- Create: `evidence/hpc-dispatcher/qualification/site-v1/SHA256SUMS`
- Create: `evidence/hpc-dispatcher/qualification/site-v1/operator-scripts/original-run_qualify.sh`
- Create: `evidence/hpc-dispatcher/qualification/site-v1/operator-scripts/original-supervise_qualify.sh`
- Create: `scripts/qualification/run_hpc_dispatcher.sh`
- Create: `scripts/qualification/supervise_hpc_dispatcher.sh`
- Create: `tests/hpc/test_qualification_operator_scripts.py`

**Interfaces:**

- `run_hpc_dispatcher.sh [qualify_hpc_dispatcher.py arguments]` executes one explicitly requested phase.
- `supervise_hpc_dispatcher.sh [qualify_hpc_dispatcher.py arguments]` retries the exact requested command up to `QUALIFY_MAX_ATTEMPTS`, without cancelling unrelated scheduler jobs.
- Runtime locks remain immutable data consumed via explicit CLI paths.

- [ ] **Step 1: Copy qualification assets without deleting their source**

Copy the 42 files below `dftworld2-qualification/evidence/hpc-dispatcher/qualification/site-v1/` into the identical main-repository path. Copy the two lock JSON files into `reference/runtime/`. Copy the original runner scripts into `operator-scripts/` using the `original-` names.

Verify the known source hashes before copying:

```text
cp2k-runtime.lock.json       0b1a36b253ec442a28fd6b45a4ecd1ec0064449f85798b32dee148530336dba6
deepmd-jax-runtime.lock.json cc7720d248cdee54434da611852fdea656aa2f213858bab646edd3c1201544b1
run_qualify.sh               e82c49f8c12ce4e4e6af2a96fb62645b66c53ccd54ac6db077e4f8f69ce3f2c4
supervise_qualify.sh         c57da00697827390c2f887e6899067f939d6a232ac389488d0d0c90b2350f004
```

- [ ] **Step 2: Write failing wrapper tests**

Create `tests/hpc/test_qualification_operator_scripts.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/qualification/run_hpc_dispatcher.sh"
SUPERVISOR = ROOT / "scripts/qualification/supervise_hpc_dispatcher.sh"


def test_operator_scripts_are_relocatable_and_do_not_cancel_user_jobs():
    for script in (RUNNER, SUPERVISOR):
        text = script.read_text()
        assert "/Users/chenxuanjie/" not in text
        assert "dftworld2-qualification" not in text
        assert "scancel" not in text
        assert "xargs" not in text


def test_runtime_lock_paths_are_canonical():
    text = RUNNER.read_text()
    assert "reference/runtime/cp2k-runtime.lock.json" in text
```

- [ ] **Step 3: Run the test to verify RED**

```bash
.venv/bin/pytest -q tests/hpc/test_qualification_operator_scripts.py
```

Expected: failure because the maintainable scripts do not exist.

- [ ] **Step 4: Implement the direct wrapper**

Create `scripts/qualification/run_hpc_dispatcher.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

qualification_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
qualification_python=${DFTWORLD_PYTHON:-"$qualification_repo_root/.venv/bin/python"}

if [[ ! -x "$qualification_python" ]]; then
  echo "Python is not executable: $qualification_python" >&2
  exit 2
fi

exec "$qualification_python" \
  "$qualification_repo_root/scripts/infra/qualify_hpc_dispatcher.py" \
  --cp2k-lock "$qualification_repo_root/reference/runtime/cp2k-runtime.lock.json" \
  "$@"
```

- [ ] **Step 5: Implement the bounded supervisor**

Create `scripts/qualification/supervise_hpc_dispatcher.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

qualification_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
qualification_max_attempts=${QUALIFY_MAX_ATTEMPTS:-6}
qualification_retry_seconds=${QUALIFY_RETRY_SECONDS:-600}
qualification_attempt=1

while (( qualification_attempt <= qualification_max_attempts )); do
  if "$qualification_repo_root/scripts/qualification/run_hpc_dispatcher.sh" "$@"; then
    exit 0
  fi
  if (( qualification_attempt == qualification_max_attempts )); then
    break
  fi
  echo "qualification attempt $qualification_attempt failed; retrying in $qualification_retry_seconds seconds" >&2
  sleep "$qualification_retry_seconds"
  qualification_attempt=$((qualification_attempt + 1))
done

echo "qualification failed after $qualification_max_attempts attempts" >&2
exit 1
```

These wrappers never imply authorization. A CP2K run still requires the caller to pass `--phase cp2k --authorized`; no plan step invokes it.

- [ ] **Step 6: Write evidence and runtime indexes**

`INDEX.md` states that the directory contains seven attempted CPU/GPU containment chains, has no sealed `receipt.json`, and therefore does not establish `formal_qualified=true`. List every run ID and fetched-log directory.

`reference/runtime/README.md` records each lock's schema, image name, remote SIF path, SHA-256, origin date, consumer command, and the rule that changing a lock requires a new file or reviewed update.

Generate `SHA256SUMS` over every qualification evidence file except `SHA256SUMS` itself, using paths relative to `site-v1/`.

- [ ] **Step 7: Verify and commit**

```bash
bash -n scripts/qualification/run_hpc_dispatcher.sh
bash -n scripts/qualification/supervise_hpc_dispatcher.sh
.venv/bin/pytest -q tests/hpc/test_qualification_operator_scripts.py \
  tests/hpc/test_qualification_receipt.py tests/hpc/test_runtime_containment.py
git diff --check
git add reference/runtime evidence/hpc-dispatcher/qualification/site-v1 \
  scripts/qualification tests/hpc/test_qualification_operator_scripts.py
git commit -m "feat(hpc): preserve qualification assets in main workspace"
```

Expected: all tests pass, all destination hashes match sources, and the original source worktree is still present.

---

### Task 4: Remove the Duplicate Qualification Worktree

**Files:**

- Remove through Git: `/Users/chenxuanjie/案例测试/dftworld2-qualification`
- Modify: `evidence/local-cleanup-20260825/decision-ledger.json`

**Interfaces:**

- Consumes the committed qualification assets from Task 3.
- Produces a single registered Git worktree.

- [ ] **Step 1: Prove no unique work remains**

Record:

```bash
git -C /Users/chenxuanjie/案例测试/dftworld2-qualification diff --exit-code
git -C /Users/chenxuanjie/案例测试/dftworld2-qualification ls-files --others --exclude-standard
git worktree list --porcelain
```

Expected: no tracked diff. Every untracked source path maps to a Task 3 destination and has the same SHA-256. Any extra path stops removal.

- [ ] **Step 2: Verify main from outside the duplicate worktree**

```bash
.venv/bin/pytest -q tests/hpc/test_qualification_operator_scripts.py \
  tests/hpc/test_qualification_receipt.py tests/hpc/test_runtime_containment.py
cd evidence/hpc-dispatcher/qualification/site-v1
shasum -a 256 -c SHA256SUMS
```

Expected: tests and hashes pass.

- [ ] **Step 3: Remove the registered worktree**

Run from `dftworld2/` only:

```bash
git worktree remove --force /Users/chenxuanjie/案例测试/dftworld2-qualification
git worktree prune
git worktree list --porcelain
```

Expected: only `/Users/chenxuanjie/案例测试/dftworld2` remains. `--force` is allowed only because Step 1 proved every untracked byte was migrated and verified.

- [ ] **Step 4: Record and commit the deletion receipt**

Append one ledger entry with classification `duplicate-worktree`, the removed HEAD, source file count, byte count, destination commit, hash verification result, and recovery command `git worktree add` at the recorded commit.

```bash
git add evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "chore(workspace): retire qualification worktree"
```

---

### Task 5: Resolve the ai2kit Reference and Root One-Off Script

**Files:**

- Inspect/remove after verification: `ai2kit/`
- Inspect/remove after verification: `/Users/chenxuanjie/案例测试/dpj_sif_pipeline.py`
- Create or update: `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/INDEX.md`
- Modify: `evidence/local-cleanup-20260825/decision-ledger.json`

**Interfaces:**

- The tracked 034 expert trajectory and source lock are the canonical scientific lineage.
- Runtime rebuild identity is owned by `base-env-build/deepmd-jax/` plus `reference/runtime/deepmd-jax-runtime.lock.json`.

- [ ] **Step 1: Seal the unusual nested Git state**

Record that `ai2kit/` has no commits, no usable HEAD, an index containing added/deleted entries, approximately 40 MB of working files, and no configured remote. Hash every file outside `ai2kit/.git/`.

- [ ] **Step 2: Compare ai2kit lineage with tracked 034 provenance**

Verify all hashes named by `034-ai2kit-water64-end-to-end-potential/reference/source.lock.json`. Compare the current `ai2kit/config/`, `workflow/`, `tools/`, geopt/AIMD trajectories, and validation outputs against the tracked `034-ai2kit-water64-end-to-end-potential/reference/expert-trajectory/` and `solution/expert/` counterparts.

Decision gate:

- if every file used by the 034 contract has a tracked byte-identical copy or a tracked source-lock hash, classify `ai2kit/` as `superseded-reference` and delete it;
- if a unique file is named by the current 034 source lock or current 042 contract, copy that exact file into the appropriate tracked `reference/` location, update the lock through its normal verifier, rerun 034/042 tests, then delete `ai2kit/`;
- files not named by either current contract are classified `historical-output` and deleted without archiving after their inventory is recorded.

- [ ] **Step 3: Prove the root SIF script is superseded**

Run:

```bash
rg -n 'dpj_sif_pipeline|tmp-dpj-sif-build' /Users/chenxuanjie/案例测试/dftworld2 \
  /Users/chenxuanjie/案例测试/skills /Users/chenxuanjie/案例测试/tests
```

Expected: no runtime caller; only historical planning/provenance references may remain. Verify that its final SIF digest `3634151fa6e0b3322b52c4c8e841a823701d19881e10cd265d1f3fd0bf8c6cdf` and source archive digest `ce095abebc155d3d28c9289f123a1772c47687d9127f78edf3472848b5b53b64` are present in `reference/runtime/deepmd-jax-runtime.lock.json`.

- [ ] **Step 4: Record provenance, then delete explicit sources**

Add the ai2kit comparison result and the `dpj_sif_pipeline.py` SHA-256 `2f7d21e4b25f4b3c97bcb31dfb73f93da67e53f8299698eacb5ef2ae4b76d5d2` to the recovery index and decision ledger. Delete only these exact paths after the gates pass:

```text
/Users/chenxuanjie/案例测试/dftworld2/ai2kit
/Users/chenxuanjie/案例测试/dpj_sif_pipeline.py
```

- [ ] **Step 5: Verify cases and commit the receipt**

```bash
.venv/bin/pytest -q 034-ai2kit-water64-end-to-end-potential/tests \
  042-go-water-dpmp/tests
git status --short
git add evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "chore(workspace): retire superseded ai2kit references"
```

Expected: no new test failures and neither deleted root path appears as untracked content.

---

### Task 6: Consolidate Recovery Records and Delete Obsolete Archive Payloads

**Files:**

- Source: `/Users/chenxuanjie/案例测试/hpc-cleanup-20260822-archive/`
- Source: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/`
- Create: `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/INDEX.md`
- Create: `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/RECOVERY.md`
- Create: `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/SHA256SUMS`
- Modify: `evidence/local-cleanup-20260825/decision-ledger.json`

**Interfaces:**

- `INDEX.md` maps each retained recovery object to current work and an owner.
- `RECOVERY.md` contains exact verification and restore commands.
- `SHA256SUMS` covers every retained recovery file by relative path.

- [ ] **Step 1: Verify all source packages before classification**

Check the four recorded archive digests:

```text
batch1-hidden-assets.tar.zst 6c6bfa05ced054066bcba5e01b019540468eba67b811fdc835e637217af927c0
batch1-old-refs.tar.zst      b550ce69eed45ffee28dba2d10d28679bdba36a34915b6e20c09ff6323f82b50
batch1-hidden-score.tar.zst  a162878d4f5f20d2a13ec63250b6e2a859e67831a79d51d3e6d00eb3d3c76ba9
batch1-gate-assets.tar.zst   275bcce87500481cc701df606c034e306ab003f6f3ef16650e8dc7cd5befa8fb
```

Also hash `batch5-small-assets.tar.zst`, verify `legacy-branches-20260823.bundle` with `git bundle verify`, verify `jobs-metadata.tar.zst`, and parse both cleanup receipts.

- [ ] **Step 2: Apply exact keep/delete gates to each large payload**

For each archive, list member paths without extraction and search current release locks, evaluator manifests, evidence manifests, 031–034 source locks, and operations docs for those paths or hashes.

Use these decisions:

- `batch1-old-refs.tar.zst`: delete when no current release/evaluator manifest refers to an archived member; its name and cleanup report already classify it as old references.
- `batch1-hidden-score.tar.zst`: delete when current 034 hidden thresholds and verifier inputs validate from Git and no current manifest requires restore from this package.
- `batch1-gate-assets.tar.zst`: delete when `034-.../tests/hidden/thresholds.json` hashes to `cf875d255e886a2d92ea46d1f755e5abd1b4cef63f623582a9bfe309d7d4797e` and the evaluator/source-lock checks pass.
- `batch5-small-assets.tar.zst`: delete when every current-contract member is byte-identical to a tracked file and no current manifest points only to the archive.
- `batch1-hidden-assets.tar.zst`: retain only if a current 031–034 release or evidence restore test still requires one of its unique members. Otherwise record the 2441-file manifest and deletion receipt, then delete the 2.7 GB payload.

No package is retained solely because it existed in the older cleanup plan; retention must be justified by a current manifest or recovery test.

- [ ] **Step 3: Classify the 453 MB Git bundle**

The bundle contains four complete refs: `codex/034-10c-qualification`, `codex/034-heterogeneous-resource-eval`, `codex/034-hpc-controller`, and `worktree-agent-aafc76406f88ca149`.

For each ref, compare its tip and patch IDs against current `main`. Keep the bundle only if it contains a commit not reachable from any intentionally retained local ref and still relevant to current 034/042/HPC work. If all relevant commits are reachable or explicitly obsolete, record `git bundle verify` output and delete the bundle.

- [ ] **Step 4: Build the minimal recovery directory**

Copy only retained objects plus all small receipts, manifests, hashes, `main-dirty.patch`, `main-dirty-disposition.md`, and jobs metadata needed to explain prior cleanup. Rewrite no historical receipt; the new index maps old absolute paths to the new recovery location.

Before deleting either old archive directory, commit the current small HPC cleanup evidence already present under `evidence/hpc-cleanup-20260822/`. Its tracked `.gitignore` intentionally excludes large extracted payloads; inspect `git status --short evidence/hpc-cleanup-20260822` and verify no archive payload or secret profile is staged.

```bash
git add evidence/hpc-cleanup-20260822
git commit -m "docs(hpc): preserve cleanup receipts and provenance"
```

`RECOVERY.md` must include:

```bash
shasum -a 256 -c SHA256SUMS
git bundle verify legacy-branches-20260823.bundle
git apply --check main-dirty.patch
```

Include the latter two commands only when those files are retained.

- [ ] **Step 5: Read back retained recovery objects**

Verify all hashes from the new directory. Fresh-extract the smallest retained tar.zst into a `mktemp -d` directory, compare its member manifest, and remove only that temporary read-back directory.

- [ ] **Step 6: Delete old archive locations after parity**

Remove only payloads marked `delete` in the ledger. When every retained source file has a verified destination, remove the now-redundant source directories:

```text
/Users/chenxuanjie/案例测试/hpc-cleanup-20260822-archive
/Users/chenxuanjie/案例测试/local-cleanup-20260823
```

If a compatibility reference remains in an immutable historical receipt, keep the receipt unchanged and document the relocation in `INDEX.md`; do not create a duplicate byte-bearing compatibility directory.

- [ ] **Step 7: Commit the classification ledger**

```bash
git add evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "chore(workspace): consolidate recovery archives"
```

---

### Task 7: Remove Rebuildable State and Empty Directories

**Files:**

- Remove: `/Users/chenxuanjie/案例测试/dftworld2/.pytest_cache/`
- Remove: `/Users/chenxuanjie/案例测试/dftworld2/tmp/`
- Remove: `/Users/chenxuanjie/案例测试/dftworld2/.venv/`
- Remove: enumerated Python `__pycache__/` and `*.pyc`
- Remove: empty `/Users/chenxuanjie/案例测试/worktrees/`
- Modify: `evidence/local-cleanup-20260825/decision-ledger.json`

- [ ] **Step 1: Prove `tmp/` contains no current work**

The current inventory shows only two build rootfs trees and interrupted `GA2O3_ALTERNATE_IS_REBASE_20260807` transfer/checksum fragments. Search tracked files, current docs, scripts, configs, and protected hashes for these names. If no current reference exists, record all three top-level tmp entries and classify them `rebuildable-or-interrupted`.

- [ ] **Step 2: Prove the environment is reproducible**

Before deleting `.venv`, run:

```bash
uv lock --check
uv sync --frozen
.venv/bin/python -c 'import dftworld_bench, pytest, yaml, pydantic'
```

Expected: all commands exit 0. Record the Python version and `uv.lock` SHA-256.

- [ ] **Step 3: Enumerate exact generated paths**

Generate a review list containing only `.pytest_cache`, `__pycache__`, `*.pyc`, `.venv`, the proven `tmp/`, and empty workspaces. Reject any non-cache path below `evidence/`, `reference/`, `solution/`, `tests/hidden/`, or `tests/fixtures/`.

Inspect `/Users/chenxuanjie/案例测试/.claude/scheduled_tasks.lock` with `lsof` and the local scheduled-task inventory. If no process holds it and no task definition uses it, classify the lock and its otherwise-empty parent as stale tool state; otherwise retain it and document its owner in the root README.

- [ ] **Step 4: Delete one explicit class at a time**

Delete in this order and record bytes after each class:

1. `.pytest_cache/`, `__pycache__/`, and `*.pyc`;
2. empty `jobs/`, `.worktrees/`, `.test-gateway-workspace/`, `.p04-smoke-workspace/`, and root `worktrees/`;
3. `dftworld2/tmp/`;
4. the stale root `.claude/scheduled_tasks.lock` and empty `.claude/`, only when Step 3's ownership check passed;
5. `dftworld2/.venv/` as a clean-environment refresh, immediately followed by Step 5.

Do not use a wildcard or delete any path absent from the reviewed list.

- [ ] **Step 5: Rebuild the environment and rerun smoke tests**

```bash
uv sync --frozen
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider \
  tests/docs/test_architecture_contracts.py \
  tests/hpc/test_qualification_operator_scripts.py \
  tests/hpc/test_qualification_receipt.py \
  042-go-water-dpmp/tests
```

Expected: environment rebuild succeeds and no test regresses.

The rebuilt `.venv/` remains because `dftworld2` is the active project. The ledger records it as `recreated-active-environment`, not reclaimed space.

- [ ] **Step 6: Commit the cleanup receipt**

```bash
git add evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "chore(workspace): remove rebuildable local state"
```

---

### Task 8: Close Known Redundant Code and Audit Remaining Candidates

**Files:**

- Inspect: repository Python, Bash, YAML, TOML, and documented CLI entry points
- Modify: `042-go-water-dpmp/instruction.md`
- Modify: `infra/runs/skill-ablation-v2.yaml`
- Create: `docs/experiments/pilot-known-limitations.yaml`
- Create: `.cluster-agents.md`
- Create: `evidence/local-cleanup-20260825/code-audit.json`
- Modify: `evidence/local-cleanup-20260825/decision-ledger.json`

**Interfaces:**

- `code-audit.json` records candidate path, symbol/entry point, inbound references, tests, decision, and replacement.

- [ ] **Step 1: Build the candidate list**

Use `rg` to inspect imports, shell invocations, YAML/TOML paths, `pyproject.toml` entry points, Dockerfile commands, CI/docs commands, and dynamic registries. Include duplicate qualification wrappers and the removed root `dpj_sif_pipeline.py`; exclude scientific fixtures, reference scripts, and frozen evidence from dead-code classification.

- [ ] **Step 2: Require four proofs before deleting code**

A candidate may be deleted only when all are true:

1. no import, command, config, registry, Dockerfile, or documentation entry points to it;
2. a current implementation owns the same required behavior, or the behavior is no longer in current scope;
3. relevant tests exercise the replacement or confirm the behavior is absent;
4. the full suite has no new failures after deletion.

Otherwise record `keep` with the unresolved reference; do not delete it. This consolidation deletes only the two already-proven redundant code surfaces: the root `dpj_sif_pipeline.py` from Task 5 and the old qualification runner copies removed with the duplicate worktree in Task 4. Any additional implementation candidate becomes a separate reviewed refactor rather than expanding this cleanup.

- [ ] **Step 3: Normalize the known current 042 formatting change**

Review the current one-character spacing change in `042-go-water-dpmp/instruction.md`. Preserve the scientific values exactly. If the missing space before `29.998` is accidental, restore table spacing through `apply_patch`; if intentional, record the reason in the code audit. Run the 042 contract tests afterward.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider 042-go-water-dpmp/tests
git add 042-go-water-dpmp/instruction.md
git commit -m "docs(042): normalize composition table formatting"
```

- [ ] **Step 4: Validate configuration readability without reverting current work**

Parse `infra/runs/skill-ablation-v2.yaml`, verify provider/credential variable names and budget fields against config tests, and retain the current MiMo configuration if tests and experiment intent agree. Do not substitute the old DeepSeek values merely to reduce the diff.

Commit `docs/experiments/pilot-known-limitations.yaml` with the experiment configuration after checking that the documented provider limitations still apply. Update `.cluster-agents.md` so it no longer claims that no CP2K runtime lock exists: point it to `reference/runtime/cp2k-runtime.lock.json`, record that the existing attempts have no sealed receipt, and keep real-site qualification fail-closed.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider \
  tests/config tests/experiments tests/hpc/test_qualification_receipt.py
git add infra/runs/skill-ablation-v2.yaml \
  docs/experiments/pilot-known-limitations.yaml .cluster-agents.md
git commit -m "chore(experiments): preserve current pilot configuration"
```

- [ ] **Step 5: Run targeted and full tests**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider \
  tests/config tests/experiments 042-go-water-dpmp/tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests
```

Expected: no new failure relative to Task 1; ideally both commands exit 0.

- [ ] **Step 6: Commit only verified cleanup changes**

```bash
git add evidence/local-cleanup-20260825/code-audit.json \
  evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "docs(workspace): record redundant code audit"
```

Before committing, inspect `git diff --cached --name-status`; only the two audit files may be staged in this step.

---

### Task 9: Final Verification, Inventory, and Maintenance Handoff

**Files:**

- Create: `evidence/local-cleanup-20260825/FINAL-REPORT.md`
- Update: `/Users/chenxuanjie/案例测试/README.md`
- Update: `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/INDEX.md`

- [ ] **Step 1: Verify repository state and worktree topology**

```bash
git status --short --branch
git worktree list --porcelain
git fsck --no-dangling
git diff --check
```

Expected: only the canonical `dftworld2` worktree is registered; all remaining changes are understood current work; Git integrity and whitespace checks pass.

- [ ] **Step 2: Verify current scientific and infrastructure work**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider \
  034-ai2kit-water64-end-to-end-potential/tests \
  042-go-water-dpmp/tests
.venv/bin/python scripts/infra/activate_v2.py --help
```

Expected: no new failures relative to Task 1, with exact pass/fail/skip counts recorded. Do not run remote qualification or formal ablation as part of cleanup.

- [ ] **Step 3: Verify evidence and recovery hashes**

```bash
cd /Users/chenxuanjie/案例测试/dftworld2/evidence/hpc-dispatcher/qualification/site-v1
shasum -a 256 -c SHA256SUMS
cd /Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08
shasum -a 256 -c SHA256SUMS
```

Expected: all retained objects report `OK`.

- [ ] **Step 4: Check old paths and sensitive material**

Search retained code/docs/config for `dftworld2-qualification`, `dpj_sif_pipeline.py`, deleted archive paths, and deleted tmp paths. Historical receipts may retain old absolute paths; current executable code may not.

Confirm `.env` is ignored and absent from Git, recovery archives, hashes, and reports. Search reports for common credential prefixes without printing secret values.

- [ ] **Step 5: Measure the result**

Record before/after top-level byte counts and calculate released space by category: duplicate worktree, ai2kit/root script, archive payloads, venv/cache/tmp, and redundant code.

- [ ] **Step 6: Write the final report**

`FINAL-REPORT.md` must contain:

- retained current work and its canonical path;
- every migrated source/destination pair;
- every deleted path, reason, size, verification, and recovery status;
- every retained archive and its current justification;
- code candidates kept and why;
- test results and any pre-existing failures;
- final Git/worktree state;
- actual disk space reclaimed.

- [ ] **Step 7: Commit the handoff**

```bash
git add evidence/local-cleanup-20260825/FINAL-REPORT.md \
  evidence/local-cleanup-20260825/decision-ledger.json
git commit -m "docs(workspace): record consolidation results"
```

Final completion requires all current-work hashes to remain valid, a single active worktree, readable ownership documentation, no unexplained top-level item, and no additional test failure.
