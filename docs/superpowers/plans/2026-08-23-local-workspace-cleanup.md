# Local Workspace Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reclaim approximately 9.5–10 GB from `/Users/chenxuanjie/案例测试` without losing uncommitted source, unique Git history, Trusted HPC archives, formal evidence, hidden fixtures, or release reproducibility.

**Architecture:** Treat Git commits/branches, Trusted evidence archives, and active worktrees as durable state. Delete only verified extraction copies, generated caches, superseded worktrees, and sealed historical job workspaces. Consolidate onto `main` only after the active Dispatcher branch is accepted.

**Tech Stack:** Git worktrees, SHA-256, tar.zst, pytest, DFTWorld evidence readers, macOS filesystem tools.

## Global Constraints

- This plan is dry-run by default; no deletion occurs merely by running an inventory command.
- Never run `git clean -fdX`: ignored paths include formal evidence workspaces, 042 hidden frames, and cleanup audit material.
- Never discard the six modified files in `dftworld2/main` without a byte-level comparison and an explicit commit/reconciliation decision.
- Keep `/Users/chenxuanjie/案例测试/hpc-cleanup-20260822-archive/*.tar.zst`, all SHA-256 manifests, relocation receipts, and verification markers.
- Keep `dftworld2/evidence/matclaw/formal` until a separately validated evidence-relocation policy supersedes it.
- Keep the active `hpc-dispatcher-simplification` worktree until it is merged into `main` and the post-merge suite passes.
- Remove Git worktrees with `git worktree remove`, never by recursively deleting their directories.
- Prunable worktree registrations may be pruned; their non-ancestor branches must not be deleted by this plan.
- Every destructive batch records before/after byte counts and exact paths in a local cleanup receipt.

---

## Current Inventory

| Path | Approximate size | Classification |
|---|---:|---|
| `dftworld2/` | 6.0 GB | main worktree; dirty, preserve |
| `dftworld2-hpc-dispatcher/` | 0.95 GB | active implementation worktree |
| `dftworld2-infra-v2-upgrade/` | 0.87 GB | superseded ancestor of Dispatcher |
| `hpc-cleanup-20260822-archive/` | 6.6 GB | 2.8 GB durable archives + 3.8 GB verified extraction copies |
| `dftworld2/jobs/` | 3.2 GB | historical Pilot workspaces; seal then delete |
| `dftworld2/.claude/worktrees/agent-aafc76406f88ca149` | 0.72 GB | clean ancestor of Dispatcher |
| `dftworld2/evidence/matclaw/formal` | 0.54 GB | durable formal evidence; preserve |
| `dftworld2/GO–water interfaces` | 0.47 GB | tracked source/data; preserve |
| `dftworld2/tmp/` | 0.11 GB | mixed user projects; no bulk deletion |

Known ancestry:

```text
infra-v2-upgrade             ancestor of hpc-dispatcher-simplification
agent worktree @ 3df3560    ancestor of hpc-dispatcher-simplification
codex/034-* branches         not all ancestors; preserve branches
```

---

### Task 1: Seal the Local Cleanup Baseline

**Files:**
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/before-du.txt`
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/git-status.txt`
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/worktrees.txt`
- Create during execution: `/Users/chenxuanjie/案例测试/local-cleanup-20260823/receipt.json`

- [ ] Record `du -sk` for every top-level directory under `/Users/chenxuanjie/案例测试`.
- [ ] Record `git status --short`, `git worktree list --porcelain`, branch tips, and `git count-objects -vH`.
- [ ] Record SHA-256 for the six dirty main-worktree files and every Trusted archive.
- [ ] Assert the active Dispatcher worktree has only expected untracked audit/generated paths.
- [ ] Assert no cleanup command targets `.git`, `evidence/matclaw/formal`, 042 hidden frames, or `ai2kit/`.

Expected result: a durable pre-cleanup inventory; zero deleted bytes.

---

### Task 2: Remove Verified Archive Extraction Copies

**Keep permanently:**

- `batch1-hidden-assets.tar.zst`
- `batch1-hidden-score.tar.zst`
- `batch1-old-refs.tar.zst`
- `batch1-gate-assets.tar.zst`
- `batch5-small-assets.tar.zst`
- all `archive-sha256-*.txt`, remote/local rehash manifests, relocation receipts, and `.migration-verified*` markers

**Delete after verification:**

- `.verify-tmp/`
- `.verify-tmp-old/`
- `.verify-tmp-hs/`
- `.verify-tmp-gate/`
- `.verify-tmp-b5/`

- [ ] Recompute every retained tar.zst SHA-256 and compare with its recorded digest.
- [ ] Confirm all migration verification markers exist.
- [ ] Confirm 034 frozen thresholds already exist in Git commit `9f72fca` with provenance.
- [ ] Record the exact extraction directories and sizes in the receipt.
- [ ] Delete only the five explicitly listed extraction directories.
- [ ] Re-run archive hash verification after deletion.

Expected reclaim: approximately 3.8–3.9 GB. The compressed Trusted archive remains intact.

---

### Task 3: Retire Superseded Worktrees Safely

**Worktrees:**

- Retire: `/Users/chenxuanjie/案例测试/dftworld2/.claude/worktrees/agent-aafc76406f88ca149`
- Retire: `/Users/chenxuanjie/案例测试/dftworld2-infra-v2-upgrade`
- Keep: `/Users/chenxuanjie/案例测试/dftworld2-hpc-dispatcher`
- Keep: `/Users/chenxuanjie/案例测试/dftworld2`

- [ ] Reconfirm `3df3560` is an ancestor of `hpc-dispatcher-simplification` and the agent worktree is clean.
- [ ] Reconfirm `infra-v2-upgrade` is an ancestor of `hpc-dispatcher-simplification`.
- [ ] Record SHA-256 and first/last lines of `dftworld2-infra-v2-upgrade/.plan.md`; it is an implemented R3 scratch plan, not production state.
- [ ] Delete only `dftworld.egg-info/` and the obsolete `.plan.md` from the infra worktree.
- [ ] Remove the clean agent worktree through `git worktree remove`.
- [ ] Remove the clean infra-v2 worktree through `git worktree remove`.
- [ ] Run `git worktree prune` to remove registrations whose directories no longer exist.
- [ ] Do not delete `infra-v2-upgrade` or any `codex/034-*` branch in this task.

Expected reclaim: approximately 1.6 GB.

---

### Task 4: Clean Generated Files by Explicit Allowlist

**Safe generated classes:**

- `.pytest_cache/`
- `__pycache__/`
- `*.pyc`
- `dftworld.egg-info/`
- `.DS_Store`
- empty `.test-gateway-workspace/`
- empty `.p04-smoke-workspace/`

- [ ] Enumerate each candidate and reject any path below `evidence/`, `tests/fixtures/`, `tests/hidden/`, `reference/`, or `solution/` unless it is exactly `__pycache__`.
- [ ] Remove generated files only from main and the active Dispatcher worktree.
- [ ] Keep both active `.venv` directories until branch consolidation.
- [ ] Run relevant scoped tests after cache deletion to prove regeneration works.

Expected reclaim: small (tens of MB); primary benefit is a clean status and safer future inventories.

---

### Task 5: Seal and Remove Historical `jobs/`

**Source:** `/Users/chenxuanjie/案例测试/dftworld2/jobs/`

Largest workspaces:

- `2026-08-19__19-04-35`: approximately 1.2 GB
- `2026-08-19__19-04-32`: approximately 1.1 GB
- `2026-08-19__17-51-32`: approximately 0.3 GB
- `_archive`: approximately 0.3 GB
- `2026-08-19__21-20-11`: approximately 0.25 GB

- [ ] Enumerate every `run-record.json`, `result.json`, resolved lock, transcript, failure code, and invalid-run reference.
- [ ] Compare run identities with `experiments/skill-ablation-v1/invalid-runs.json` and the HPC cleanup job-metadata archive.
- [ ] Create a compact metadata-only tar.zst outside Git and a per-file SHA-256 manifest.
- [ ] Fresh-extract the metadata archive and parse every JSON document.
- [ ] Confirm no released manifest or active experiment references workspace bytes inside local `jobs/`.
- [ ] Delete `jobs/` only after all preceding checks pass.
- [ ] Keep the compact metadata archive and receipt outside Git; commit only the small index if release policy requires it.

Expected reclaim: approximately 3.2 GB.

---

### Task 6: Reconcile the Dirty Main Worktree

**Do not delete or reset these files:**

- `032-matclaw-cips-curie-temperature/Dockerfile`
- `033-matclaw-cips-domain-wall-search/Dockerfile`
- `dftworld_bench/agents.py`
- `experiments/skill-ablation-v1/invalid-runs.json`
- `scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/validate_case.py`
- `scripts/matclaw_hpc_gateway.py`

- [ ] Compare each dirty file against `hpc-dispatcher-simplification` and identify its owning commit.
- [ ] Preserve the unique local `invalid-runs.json` content and its provenance.
- [ ] Commit legitimate changes in focused commits or explicitly supersede them through the Dispatcher merge.
- [ ] Decide whether small cleanup metadata under `evidence/hpc-cleanup-20260822/manifest/` should be committed; never commit archive bytes or extracted hidden assets.
- [ ] Keep untracked `ai2kit/` and mixed `tmp/` out of automated cleanup.
- [ ] Reach a clean main worktree before merging the Dispatcher branch.

Expected reclaim: negligible; this is the data-loss prevention gate for branch consolidation.

---

### Task 7: Merge Dispatcher and Remove the Final Duplicate Worktree

- [x] Finish receipt-verifier hardening and keep the full suite green while D11 remains operationally blocked. (done 2026-08-23: verdict-free receipt schema + derivation verifier, 23 fixture tests)
- [ ] Review and merge `hpc-dispatcher-simplification` into clean `main` without squashing away task-local history.
- [ ] Run the full test suite from main outside the socket-restricted sandbox.
- [ ] Run activation preflight and confirm D11 is `NOT_RUN` or `BLOCKED_SITE_ACL`, never falsely PASS.
- [ ] Confirm main contains all Dispatcher commits and the implementation worktree is clean.
- [ ] Remove `/Users/chenxuanjie/案例测试/dftworld2-hpc-dispatcher` with `git worktree remove`.
- [ ] Keep one main `.venv`; remove the redundant worktree environment with the worktree.

Expected reclaim: approximately 0.95 GB.

---

### Task 8: Review Unique Legacy Branches Without Deleting Them Blindly

Branches requiring separate review:

- `codex/034-10c-qualification`
- `codex/034-heterogeneous-resource-eval`
- `codex/034-hpc-controller`

- [ ] Produce `git log main..BRANCH`, changed-file lists, and patch IDs for each branch.
- [ ] Classify commits as merged-equivalent, obsolete, or unique scientific evidence/code.
- [ ] Create a Git bundle containing every unique branch before any branch deletion.
- [ ] Delete a branch only through a separate, explicit authorization after bundle verification.
- [ ] Run `git gc` only after branch decisions and a verified bundle; do not expire reflogs in this cleanup plan.

Expected reclaim: unknown; Git pack is currently approximately 453 MB, so the maximum gain is modest.

---

## Explicit Preserve List

- `/Users/chenxuanjie/案例测试/hpc-cleanup-20260822-archive/*.tar.zst`
- archive hashes, manifests, relocation receipts, and migration markers
- `dftworld2/evidence/matclaw/formal/`
- `dftworld2/evidence/hpc-cleanup-20260822/manifest/`
- all tracked 001–042 Case source
- 034 restored frozen thresholds and provenance
- 042 hidden frames and test fixtures
- `GO–water interfaces/`
- untracked `ai2kit/`
- mixed user-owned `tmp/` until separately inventoried
- active Dispatcher branch/worktree until merge
- all non-ancestor `codex/034-*` branches until bundle/review

## Forbidden Bulk Operations

```text
git clean -fdX
git reset --hard
rm -rf /Users/chenxuanjie/案例测试
rm -rf dftworld2/evidence
rm -rf dftworld2/tmp
rm -rf dftworld2/.git
```

## Completion Gates

- [ ] Trusted tar.zst archives and hashes still verify.
- [ ] No formal evidence, hidden fixture, source change, or unique branch was lost.
- [ ] Main and active worktree status are understood and clean at their merge gate.
- [ ] Full tests pass after consolidation.
- [ ] Activation remains fail-closed at D11 until a valid real receipt exists.
- [ ] `git worktree list` contains only real, intentionally retained worktrees.
- [ ] Local cleanup receipt lists every deleted path and before/after bytes.
- [ ] Expected total reclaim is approximately 9.5–10 GB.

## Recommended Execution Order

```text
Task 1 seal
-> Task 2 extracted archive copies
-> Task 3 superseded worktrees
-> Task 4 generated caches
-> Task 5 historical jobs
-> Task 6 main reconciliation
-> Task 7 Dispatcher merge and duplicate worktree removal
-> Task 8 optional legacy-branch review
```
