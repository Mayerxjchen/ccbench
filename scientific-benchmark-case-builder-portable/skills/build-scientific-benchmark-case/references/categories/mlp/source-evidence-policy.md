# Source and Evidence Policy

> Load this when discovering sources, attaching provenance, resolving source priority, or recording conflicts, access, and license evidence.

## Principle

A reproduction specification is only as trustworthy as the identity and provenance of the artifacts behind it. Keep **claim status**, **source kind**, and **conflict state** separate.

## Source identity

Assign every source a stable `source_id` in `source-evidence-map.yaml`.

Example:

```yaml
sources:
  paper-001:
    source_kind: paper
    title: Example paper
    doi: 10.xxxx/example
    sha256: null

  si-001:
    source_kind: supporting_information
    parent_source: paper-001
    sha256: abc123...

  repo-001:
    source_kind: repository_config
    repository: https://example.invalid/repo
    commit: 0123456789abcdef
```

Use immutable identity when possible:

- paper/SI: DOI + local file SHA-256;
- repository: exact commit, release tag, or content hash;
- dataset: DOI/record ID + version + archive/file hash when available;
- model artifact: file hash + producing release/run identity.

A URL alone is not immutable identity.

## Source kinds

Canonical values:

```text
paper
supporting_information
repository_config
repository_code
repository_release
dataset_metadata
model_artifact
author_documentation
external_reference
```

`source_kind` answers **where the evidence came from**, not whether the claim is certain.

## Claim status

Canonical values:

```text
observed
derived
inferred
unknown
conflicting
```

- `observed`: directly stated in the cited source or directly present in a version-matched executable artifact.
- `derived`: deterministic calculation or transformation from observed evidence.
- `inferred`: interpretation that is plausible but not directly stated.
- `unknown`: unresolved after a proportionate search.
- `conflicting`: relevant sources disagree and no justified resolution exists.

Do not use source kinds as claim status and do not use `conflicting` as a source kind.

## Evidence records

Example:

```yaml
evidence:
  ev-model-cutoff-si:
    source_id: si-001
    source_kind: supporting_information
    locator:
      page: 7
      section: 1.3 ML Model Constructions
    claim: model.architecture.cutoff
    note: cutoff reported in SI

  ev-model-cutoff-config:
    source_id: repo-001
    source_kind: repository_config
    locator:
      commit: 0123456789abcdef
      path: configs/train.yaml
      yaml_path: model.cutoff
    claim: model.architecture.cutoff
```

Keep excerpts short. Prefer source locators over copying long text.

## Source priority

Use this priority as a search heuristic, not an automatic conflict resolver:

```text
version-matched executable artifact
    > version-matched dataset/model metadata
    > Supporting Information
    > Methods section
    > main paper
    > repository artifact not proven to match the reported target
    > inference
```

A repository config is "version-matched" only when evidence links its commit/release/artifact to the reported target model or publication snapshot.

Do not assume the current `main` branch represents the published model.

## Conflict model

Conflicts are first-class records:

```yaml
conflicts:
  - conflict_id: conflict-001
    field: model.architecture.cutoff
    severity: critical
    evidence:
      - ev-model-cutoff-si
      - ev-model-cutoff-config
    resolution:
      status: unresolved
      selected_evidence: null
      rationale: null
```

Canonical resolution statuses:

```text
unresolved
resolved
waived
```

A conflict may be resolved only when additional evidence supports the selected value or the reproduction scope explicitly permits a documented choice. Record the rationale and selected evidence.

Never resolve by:

- taking an average;
- picking the more convenient value;
- assuming config always wins;
- assuming SI always wins;
- choosing a common default.

## Criticality

A conflict is `critical` when it can change execution identity or verification outcome for the requested scope, for example:

- target model identity;
- dataset identity/split;
- reference-theory fingerprint;
- model architecture/cutoff;
- training objective or essential schedule;
- verification dataset or metric definition.

Cosmetic metadata conflicts can be `noncritical`.

## Access policy

Track availability separately from license:

```yaml
access:
  dataset:
    status: restricted
```

Canonical access states:

```text
available
restricted
unavailable
unknown
```

"Public" in prose does not prove that the artifact is currently downloadable, version-stable, or redistributable.

## License policy

Track code, data, model, and paper licenses independently.

Unknown license information:

- does not automatically block private/internal reproduction;
- should remain an explicit unknown;
- may block or limit public benchmark redistribution.

Do not infer a dataset license from a repository code license.

## Stop condition for source search

Stop searching when either:

1. all fields required by the requested reproduction scope have sufficient evidence for execution and verification; or
2. unresolved blockers are precise and additional source searching is no longer proportionate without a new artifact, author clarification, or access grant.

Do not keep reading unrelated scientific discussion after the reproduction-critical information is sufficient.
