# Case-builder worktree audit (2026-09-09)

The protected worktree `case-builder/research-v2.4` was 23 commits ahead of
the infra MVP base. Its tip is retained at
`refs/backup/infra-mvp-casebuilder-20260909` and in
`<external-evidence-root>/bench-mvp-20260909.bundle`.

The commits were reviewed for the final infra boundary. The research-case
builder, research fixtures, JAX-GPU assets, and qualification documentation
remain historical/optional work; they are not Candidate runtime dependencies
and are not migrated into the host-Claude MVP. The only relevant boundary
changes (request-only skill, profiles, case contract, host pilot, and
verifier-only image targets) are implemented on the current feature branch.

Unique commits retained by the protection ref:

```text
d48a0ee 816350c f822b7c c911aa3 8498a89 3d5190d 5233bb7 f122d26
d19d8a6 97ebd72 e264958 31ea0c4 ecdf07d bb950a0 9c32548 5d6d97b
2d7b813 bae55b2 f8c148a 8d95ae8 86f1277 2c230e5 fb4dde4
```

Disposition: preserve for possible future case-builder integration; do not
make it an active Candidate or Operator path. The worktree may be removed
only after the new bundle has been restored and the Git worktree metadata and
object database have passed integrity checks.
