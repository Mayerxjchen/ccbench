# scientific-benchmark-case-builder-portable

Portable builder for scientific benchmark cases. Ships exactly one Skill,
`build-scientific-benchmark-case`, with the MLP (machine learning interatomic
potential) category as the sole shipped category.

Version 2.1.0.

Version 2.1 adds a low-cost quality funnel before formal benchmark release:

```text
Runnable Draft -> real Discovery -> REJECT / REFINE / PROMOTE
```

It also ships deterministic MLP cross-layer checks for public system scope,
hidden artifacts, public recipe paths, and source-lock hashes. Expert
reference, frozen thresholds, and formal Verifier closure remain post-PROMOTE
work; Draft readiness never sets `benchmark_valid=true`.

## Install

```bash
bash install.sh --host claude-code --scope project
```

Installs exactly one Skill at `~/.claude/skills/build-scientific-benchmark-case/`.
The installation is staged, validated, and atomically swapped: a failed copy or
validation leaves any previous installation intact.

## Migration from v1.1

The legacy `literature-to-mlp-spec` skill (v1.1) is **not** deleted by the
default install. It is preserved and a warning is printed:

```text
old command: /literature-to-mlp-spec
new command: /build-scientific-benchmark-case mode=extract-spec category=mlp
```

To remove the legacy skill explicitly:

```bash
bash install.sh --remove-legacy
```

## Verification

```bash
bash install.sh --check
bash -n install.sh
uv run python -m unittest -v tests/test_portable_package.py
uv run python -m unittest -v tests/test_common_builder.py
uv run python -m unittest -v tests/test_mlp_category.py
uv run python -m pytest -q tests/test_discovery_quality.py
```

## Layout

```text
install.sh
manifest.json
README.md
SHA256SUMS
skills/build-scientific-benchmark-case/
  SKILL.md
  references/          # common + mlp policies, category-registry.yaml
  scripts/             # common + mlp builder scripts
  assets/case-template/ # common / execution / category overlays
tests/                 # unittest suites and fixtures
```

`SHA256SUMS` lists every file except itself exactly once; `install.sh --check`
verifies the listing and hashes.

## Discovery and quality checks

```bash
python skills/build-scientific-benchmark-case/scripts/categories/mlp/check_draft_consistency.py CASE --json
python skills/build-scientific-benchmark-case/scripts/common/classify_failure.py --run RUN_RECORD.json
```

The consistency checker is read-only. It reports stale hidden systems,
manifest scope/count drift, public recipe systems outside the public contract,
and source-lock hash mismatches. The classifier prevents source, case-design,
runtime, resource, or infrastructure defects from being misreported as Agent
failure.

## Optional dftworld target

`scaffold` mode accepts an optional `--target dftworld`. When set, and when the
repository runtime is present, the portable builder hands the scaffolded draft
to the dftworld Case Factory CLI

```bash
python -m dftworld_bench.case_factory render CASE --target dftworld
```

which renders the executable contract: `task.toml` (schema 1.2),
`Dockerfile`, `.dockerignore` and `source/dftworld-target.lock.json`. The
adapter never duplicates builder logic and never touches scientific content.
Outside the dftworld repository, or when the adapter rejects the design, target
mode fails clearly and preserves the portable scaffold. `benchmark_valid`
remains false regardless.
