# Bench case template

Use `bench case build` from a validated Case IR to create a draft with this
layout:

```text
<case-id>/
├── case.toml              # public contract and explicit Candidate allowlist
├── task.md                # canonical task description (public)
├── input/                 # public task inputs only
├── environment/           # private task Dockerfile/runtime recipe
├── solution/              # private reference solution
└── tests/                 # private verifier launcher and complete tests bundle
```

Only `task.md`, the instruction mapped to `instruction.md`, and files named by
`[candidate].files` (normally `input/**`) are exported to Candidate Docker.
`environment/`, `solution/`, `tests/`, `reference/`, and legacy `verifier/` remain
maintainer-side. Do not add a whole-case copy rule or mount the private
directories into Candidate.

The generated `environment/Dockerfile`, `solution/solve.sh`, and
`tests/test.sh` are intentionally failing placeholders. Replace them with a
pinned runtime recipe, a private reference implementation, and the common
verifier launcher before running `bench case validate`.

The worker mounts `tests/` as its complete private verifier bundle. Existing
Bench cases may keep their canonical `case.toml`, `task.md`, `input/`, and
`verifier/` layout; the three private authoring directories are an additive
template, not a required migration of old cases.
