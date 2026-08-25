# Workspace Maintenance Policy

Scope: this repository (`dftworld2/`) and the local workspace that hosts it
(`/Users/chenxuanjie/案例测试`). The consolidation of 2026-08-25
(`docs/superpowers/plans/2026-08-25-workspace-consolidation.md`) established a
single canonical worktree and one retention policy per top-level item.

## Retention classes

Every retained path belongs to exactly one class:

| Class | Meaning | Examples |
| --- | --- | --- |
| `source` | Tracked code, cases, schemas, scripts, docs. Lives in Git; rebuildable from history. | case dirs `001`–`042`, `scripts/`, `tests/`, `docs/` |
| `current-evidence` | Active scientific evidence that is *not* reproducible from Git. | `042-go-water-dpmp/`, `evidence/hpc-cleanup-20260822/`, `evidence/hpc-dispatcher/qualification/site-v1/`, `docs/experiments/`, `reference/runtime/*.lock.json` |
| `rebuildable` | Derived state that may be deleted at any time. | `.venv/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `tmp/`, empty workspace directories |
| `recovery` | Verified Git bundle / patch / manifest plus only those archive payloads a current release still needs. | `/Users/chenxuanjie/案例测试/archives/workspace-recovery-2026-08/` |
| `external-reference` | Independent projects outside Git that current work reads but does not own. | root `skills/` project, root `tests/` project, root DOCX, and the 034 `ai2kit/` lineage until its comparison gate closes |

## Protected areas

These paths carry current work and must never be deleted or rewritten by a
cleanup pass:

- `042-go-water-dpmp/` — active Runnable-Draft case (instruction, tests, contracts).
- `infra/runs/skill-ablation-v2.yaml` — current pilot experiment configuration.
- `evidence/hpc-cleanup-20260822/` — receipts and provenance of the 2026-08-22 HPC disk cleanup.
- `evidence/hpc-dispatcher/qualification/site-v1/` — seven attempted CPU/GPU containment chains (no sealed receipt yet).
- `evidence/local-cleanup-20260825/decision-ledger.json` — append-only deletion ledger.
- `reference/runtime/` — immutable runtime locks consumed via explicit CLI paths.

Changing bytes under a protected area requires its own reviewed commit; cleanups
may not "tidy" them.

## Deletion rules

1. A source is deleted only after the destination exists, hashes match
   (`shasum -a 256 -c …` reports `OK`), path references are resolved, and the
   relevant tests pass.
2. Every deletion batch appends an entry to
   `evidence/local-cleanup-20260825/decision-ledger.json` with `path`,
   `classification`, `reason`, `bytes`, `verification`, `recovery`, `status`.
   The ledger is append-only: earlier entries are never removed.
3. Never use `git clean`, `git reset --hard`, `git checkout --`, broad recursive
   deletion, or wildcard deletion. Delete enumerated explicit paths only.
4. A linked worktree directory is **never** removed directly from the
   filesystem. Use:

   ```bash
   git worktree list --porcelain      # inventory first
   git worktree remove <path>         # then prune if needed
   git worktree prune
   ```

5. Do not submit, cancel, or delete remote HPC jobs or files from a local
   cleanup. Local cleanups change local files only.
6. `.env` is never committed, archived, hashed into manifests, or printed.

## Cache policy

Rebuildable state is safe to reclaim at any time, with two rules:

```bash
# caches and bytecode (safe anytime)
find . -type d -name __pycache__ -not -path './.venv/*' -prune -print   # review, then delete explicitly
rm -rf .pytest_cache

# full environment refresh — verify reproducibility first
uv lock --check && uv sync --frozen
```

`tmp/` holds interrupted transfers and build scratch; inspect before deleting,
and record what was found in the decision ledger. After deleting `.venv/`,
rebuild immediately with `uv sync --frozen` because this checkout is the active
project.

## Evidence retention

Evidence is cheap to keep and expensive to reconstruct. Receipts, manifests,
hash lists, audit logs, fetched scheduler logs, and runtime locks stay forever
unless superseded by a newer reviewed artifact that names their successor.
Large payloads are kept only when a current manifest or recovery test names
them; otherwise they are recorded in the decision ledger (with their manifest)
and deleted.

## Archive decision rules

A payload under `archives/` is retained only when at least one of these holds:

1. a current release lock, evaluator manifest, or evidence manifest names a
   member path or hash;
2. a restore/recovery test in `tests/` consumes it;
3. it is the sole verified copy of work referenced by an immutable historical
   receipt.

"No package is retained solely because it existed in an older cleanup plan."
Before deleting any archive payload, record its member manifest and hash in the
ledger, and read back one freshly extracted copy into a `mktemp -d` directory.
