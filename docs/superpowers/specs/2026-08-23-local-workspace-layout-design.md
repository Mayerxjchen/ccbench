# Local Workspace Layout Design

**Status:** Approved design
**Date:** 2026-08-23
**Scope:** `/Users/chenxuanjie/案例测试` and its DFTWorld-adjacent local assets

## Goal

Turn the current mixed directory into a maintainable workspace where source,
temporary execution state, Trusted evidence, independent projects, documents,
and branch archives have explicit ownership and test procedures.

The reorganization must not change DFTWorld Case paths, invalidate Git
worktrees, break evidence hashes, expose hidden assets, or make tests depend on
the operator's current working directory.

## Chosen Approach

Use conservative layering:

- keep `/Users/chenxuanjie/案例测试/dftworld2` at its current path;
- keep 001–042 Case directories at the DFTWorld repository root;
- classify everything outside the repository by durability and ownership;
- move assets in independently verifiable batches;
- preserve compatibility paths while receipts or scripts still refer to them;
- keep deletion in the separate local-cleanup plan.

The workspace root remains a plain directory, not a Git superproject. Canonical
workspace tooling and schema live in `dftworld2`, while generated navigation
files live at the workspace root.

## Target Layout

```text
/Users/chenxuanjie/案例测试/
├── README.md
├── workspace.toml
│
├── dftworld2/                       # sole active Benchmark repository
│   ├── 001-* ... 042-*              # stable Case paths
│   ├── dftworld_bench/
│   ├── tests/
│   ├── evidence/
│   ├── releases/
│   └── experiments/
│
├── worktrees/
│   └── dftworld2/                   # temporary linked worktrees
│
├── trusted-evidence/                # private, never Candidate-visible
│   ├── hpc/2026-08-22/
│   │   ├── archives/
│   │   ├── manifests/
│   │   └── relocation-receipts/
│   └── local-cleanup/2026-08-23/
│
├── workspace-state/                 # disposable/reproducible
│   ├── runs/
│   ├── tmp/
│   ├── cache/
│   └── venvs/
│
├── projects/
│   └── electromind-skills/
│       ├── skills/
│       └── tests/
│
├── external/
│   └── ai2kit/
│
├── documents/
│   └── 计算化学案例构造与评测_031-034增补版.docx
│
└── archives/
    └── git-bundles/
```

## Ownership Rules

### DFTWorld Repository

Owns versioned benchmark code, Cases, tests, schemas, manifests, release
metadata, and small evidence records. It does not own local model outputs,
temporary workspaces, private HPC archives, external source checkouts, or
personal documents.

### Trusted Evidence

Owns hidden/reference archives, relocation receipts, cleanup receipts, and
byte manifests. It remains outside Git and is excluded from Candidate bundles.
Every move is content-preserving and produces a second relocation receipt.

The old `hpc-cleanup-20260822-archive` path remains as a compatibility symlink
until all consumers use the canonical Trusted path.

### Workspace State

Owns runs, temporary files, caches, and virtual environments. Everything below
this root is reproducible or has an explicit retention deadline. DFTWorld uses
a local operator configuration to resolve this root; Cases and Skills do not
hard-code it.

### Independent Projects

The current root-level `skills/` and `tests/` form one ElectroMind Skills
project and move together. Its test baseline must be recorded before the move
and reproduced afterward. Missing fixtures are a project finding, not a reason
to mix the project back into DFTWorld.

### External Assets

The untracked `dftworld2/ai2kit` tree moves only after dependency and provenance
inspection. A temporary compatibility symlink is allowed if local tools still
refer to the old path. External assets never become accidental Git additions.

### Worktrees

Active linked worktrees live under `worktrees/dftworld2/<branch-slug>`. They
are created, moved, and removed only through Git worktree commands. Current
state has only the main worktree; empty category directories are acceptable.

## Workspace Manifest

`workspace.toml` is the local operator map. It contains no secrets and records:

- canonical category paths relative to the workspace root;
- project names and kinds;
- test commands and working directories;
- retention class (`durable`, `trusted`, `reproducible`, `external`);
- compatibility symlinks;
- expected active Git worktrees.

The schema and checker are versioned inside DFTWorld. The root manifest is
machine-local and may contain absolute-root identity, but no credentials.

## Test Model

One checker supports three levels:

1. `check`: paths, ownership, symlink targets, Git state, and forbidden mixing;
2. `test --project NAME`: run one project's declared test command;
3. `test --all`: run DFTWorld core tests, portable Skill tests, and the
   independent ElectroMind Skills tests without sharing output directories.

Test outputs go to `workspace-state/runs/tests/<timestamp>`. A test never writes
into Trusted evidence or a Case fixture tree.

## Migration Safety

- inventory and hash before every move;
- move one category at a time;
- verify byte counts and hashes at the destination;
- preserve a compatibility symlink when an absolute path is still referenced;
- update consumers, rerun tests, then remove compatibility only in a later
  reviewed step;
- stop on cross-device partial moves, hash mismatch, dirty worktree, unknown
  symlink, or a source path referenced by a release manifest;
- never combine a move with evidence deletion.

## Explicit Non-Goals

- moving 001–042 under a new `cases/` directory;
- turning the workspace root into a Git superproject;
- rewriting old immutable evidence manifests;
- moving formal evidence out of `dftworld2/evidence` in this project;
- deleting unique branches or archives;
- consolidating all virtual environments into one mutable environment;
- changing scientific thresholds, Case identity, or release contents.

## Completion Criteria

- the workspace root contains only the declared categories and navigation
  files;
- `dftworld2` remains at its original path and full tests remain green;
- Trusted archives verify at their canonical location and through the legacy
  compatibility link;
- independent Skills tests behave identically before and after migration;
- runtime state is outside the repository and can be deleted without touching
  source or evidence;
- no external checkout is untracked inside DFTWorld;
- the checker reports no ownership violations;
- one documented command can check layout and run all declared test suites.
