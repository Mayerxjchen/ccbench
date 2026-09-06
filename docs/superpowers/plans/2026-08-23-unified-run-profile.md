# Unified Run Profile Configuration Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **Status:** Superseded by `2026-08-23-single-run-config.md`; do not execute.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one named Run Profile the sole model/API/Agent/experiment/runtime/site policy input and ensure runtime behavior exactly matches the immutable resolved lock.

**Architecture:** Extend the profile registry with typed Run/Model/API schemas, resolve references into a secret-free `ResolvedRunConfig`, and resolve secret bytes separately in trusted code. Wire the resolved object into pagent provider creation, `RetryingModelClient`, Harness budgets, CLI admission, and pair validation. Formal/Pilot reject policy overrides; Smoke/Discovery overrides are non-counted.

**Tech Stack:** Python 3.12+, TOML/tomllib, JSON Schema 2020-12, pagentv4, pytest, SHA-256.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-08-23-unified-run-profile-design.md`.
- No Case file gains model, API, budget, treatment, runtime implementation, or site fields.
- No secret value is serialized, logged, sent to Candidate, or stored in a lock.
- Pilot/Formal CLI overrides fail before Docker, model provider, or HPC startup.
- Smoke/Discovery override results are always `counted=false`.
- Runtime values and lock values must originate from one `ResolvedRunConfig` instance.
- Existing RunRecord readers remain compatible.
- No provider fallback or endpoint switching is introduced.
- Every behavior change follows RED→GREEN and a task-local commit.

---

### Task 1: Add Typed Run, Model, and API Profiles

**Files:**
- Create: `infra/config/run-profiles.toml`
- Modify: `infra/config/model-profiles.toml`
- Modify: `infra/config/api-profiles.toml`
- Create: `infra/schemas/run-profile.schema.json`
- Create: `infra/schemas/model-profile.schema.json`
- Create: `infra/schemas/api-profile.schema.json`
- Modify: `dftworld_bench/config/profiles.py`
- Modify: `tests/config/test_profiles.py`

**Interfaces:**
- `ProfileRegistry.load(root)` validates agents, run_profiles, models, and api.
- `ProfileRegistry.digest(kind, name)` remains the identity primitive.

- [ ] **Step 1: Write failing validation tests**

Add tests proving unknown run modes, missing references, unknown provider,
literal credential values, zero attempts, negative timeout, and duplicate
profiles fail. Add a positive test loading the real config directory.

```python
def test_api_profile_rejects_literal_secret(tmp_path):
    write_profiles(tmp_path, '[api.bad]\nprovider="deepseek"\ncredential="sk-x"\n')
    with pytest.raises(ValueError, match="credential"):
        ProfileRegistry.load(tmp_path)

def test_real_run_profiles_load():
    registry = ProfileRegistry.load(ROOT / "infra/config")
    assert registry.require("run_profiles", "pilot-hpc-deepseek-v4")["mode"] == "pilot"
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/config/test_profiles.py`

Expected: failures because schemas and `run_profiles` config do not exist.

- [ ] **Step 3: Add schemas and profiles**

Define the exact profiles from the design. Replace the unused generic API env
names with provider-specific names and use `max_attempts`, not mixed
`max_retries` semantics. Keep a `local-smoke-deepseek` compatibility profile.

- [ ] **Step 4: Generalize schema validation**

Map profile kinds to schema files in `ProfileRegistry.load()`. Reject unknown
keys through each schema's `additionalProperties: false`.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest -q tests/config/test_profiles.py tests/config/test_secrets.py`

Expected: PASS.

```bash
git add infra/config/run-profiles.toml infra/config/model-profiles.toml \
  infra/config/api-profiles.toml infra/schemas/run-profile.schema.json \
  infra/schemas/model-profile.schema.json infra/schemas/api-profile.schema.json \
  dftworld_bench/config/profiles.py tests/config/test_profiles.py
git commit -m "feat(config): add typed unified run profiles"
```

---

### Task 2: Resolve Run Profiles and Secrets Separately

**Files:**
- Modify: `dftworld_bench/config/resolver.py`
- Modify: `dftworld_bench/config/secrets.py`
- Modify: `tests/config/test_resolver.py`
- Modify: `tests/config/test_secrets.py`

**Interfaces:**

```python
resolve_run_profile(name: str, registry: ProfileRegistry,
                    overrides: RunOverrides | None = None) -> ResolvedRunConfig
resolve_api_secrets(config: ResolvedRunConfig,
                    provider: EnvSecretProvider) -> ResolvedApiSecrets
```

- [ ] **Step 1: Write RED resolution tests**

Tests require reference resolution, all subprofile digests, model/API provider
equality, missing references, override rules by mode, and secret redaction.

```python
def test_formal_rejects_model_override(registry):
    with pytest.raises(FrozenExperimentOverrideError, match="model"):
        resolve_run_profile(
            "formal-hpc-deepseek-v4", registry,
            RunOverrides(model="deepseek/other"),
        )

def test_smoke_override_is_non_counted(registry):
    resolved = resolve_run_profile(
        "local-smoke-deepseek", registry, RunOverrides(max_turns=8)
    )
    assert resolved.override_present is True
    assert resolved.frozen is False
    assert resolved.counted is False
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/config/test_resolver.py tests/config/test_secrets.py`

Expected: import failures for new types/functions.

- [ ] **Step 3: Implement immutable resolved types**

Add `RunOverrides`, `ResolvedModelConfig`, `ResolvedApiConfig`,
`ResolvedAgentConfig`, `ResolvedRunConfig`, and `ResolvedApiSecrets`. The
serializable config contains env names and policy but no values.

- [ ] **Step 4: Resolve and bind references**

Require provider equality between model and API profiles. Compute a digest for
the Run Profile body plus each referenced profile. Pilot/Formal reject any
override; Smoke/Discovery apply only model/max-turns overrides.

- [ ] **Step 5: Resolve secrets lazily**

Build `EnvSecretProvider` from the resolved API profile. Return `SecretValue`
for key and endpoint. Endpoint may be public when revealed to provider but is
not serialized into the public config or lock.

- [ ] **Step 6: Verify GREEN and commit**

Run: `.venv/bin/pytest -q tests/config/test_resolver.py tests/config/test_secrets.py`

Expected: PASS.

```bash
git add dftworld_bench/config/resolver.py dftworld_bench/config/secrets.py \
  tests/config/test_resolver.py tests/config/test_secrets.py
git commit -m "feat(config): resolve run policy and trusted secrets"
```

---

### Task 3: Add Secret-Free Configuration CLI

**Files:**
- Create: `dftworld_bench/config/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/config/test_config_cli.py`
- Modify: `README.md`

**Interfaces:**
- Console command: `dftworld-config`
- Subcommands: `resolve PROFILE`, `doctor PROFILE`, `diff A B`.

- [ ] **Step 1: Write RED CLI tests**

Test JSON output, missing secret detection, unsupported provider, profile diff,
and a recursive assertion that secret values never occur in stdout/stderr.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/config/test_config_cli.py`

Expected: module/entry-point missing.

- [ ] **Step 3: Implement CLI**

`resolve` prints resolved policy and digests. `doctor` additionally checks env
presence and provider support without constructing Candidate/HPC resources.
`diff` recursively reports changed resolved paths. All commands return typed
JSON with nonzero status on invalid configuration.

- [ ] **Step 4: Register entry point and docs**

```toml
[project.scripts]
dftworld-config = "dftworld_bench.config.cli:main"
```

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest -q tests/config/test_config_cli.py`

Expected: PASS.

```bash
git add dftworld_bench/config/cli.py tests/config/test_config_cli.py \
  pyproject.toml README.md
git commit -m "feat(config): add run profile resolve and doctor CLI"
```

---

### Task 4: Wire Resolved Model/API/Agent Policy into Runtime

**Files:**
- Modify: `dftworld_bench/agents.py`
- Modify: `dftworld_bench/core/model_transport.py`
- Modify: `eval.py`
- Modify: `tests/core/test_model_transport.py`
- Modify: `tests/core/test_production_wiring_r2.py`
- Create: `tests/config/test_runtime_profile_wiring.py`

**Interfaces:**
- `make_provider(model: ResolvedModelConfig, secrets: ResolvedApiSecrets)`.
- `PagentAdapter` consumes `ResolvedRunConfig` plus trusted secrets.
- `RetryingModelClient` consumes the resolved API retry policy.

- [ ] **Step 1: Write RED production-path tests**

Inject fake pagent provider classes and assert actual constructor arguments are
profile model ID, endpoint, and key. Assert retry client gets max attempts,
delays, and timeout. Assert runner, HarnessSpec, ledger, and lock share one turn
value.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/config/test_runtime_profile_wiring.py tests/core/test_model_transport.py tests/core/test_production_wiring_r2.py`

Expected: runtime still uses raw CLI/default constructor values.

- [ ] **Step 3: Refactor provider construction**

Make `make_provider()` accept resolved model/API inputs and explicitly pass
`base_url` and `apikey` to pagent. Keep revealed values in local variables only;
never attach them to adapter logs or RunRecord.

- [ ] **Step 4: Wire retry policy**

Construct `RetryingModelClient` with `max_attempts`, backoff, and timeout from
the resolved API config. Remove reliance on constructor defaults in production.

- [ ] **Step 5: Wire Agent budgets**

Use resolved `max_model_turns` for pagent Agent, PagentAdapter, HarnessSpec,
budget ledger, logical request budget, and sampling digest.

- [ ] **Step 6: Verify GREEN and commit**

Run the RED suite plus `tests/test_workspace_isolation.py`.

Expected: PASS; secret leak tests remain green.

```bash
git add dftworld_bench/agents.py dftworld_bench/core/model_transport.py eval.py \
  tests/config/test_runtime_profile_wiring.py tests/core/test_model_transport.py \
  tests/core/test_production_wiring_r2.py tests/test_workspace_isolation.py
git commit -m "refactor(runtime): consume resolved model api and agent policy"
```

---

### Task 5: Make Run Profile the Eval Admission Contract

**Files:**
- Modify: `eval.py`
- Modify: `dftworld_bench/config/resolver.py`
- Modify: `infra/schemas/resolved-run-lock.schema.json`
- Modify: `tests/config/test_resolver.py`
- Create: `tests/config/test_eval_run_profile.py`
- Modify: `tests/experiments/test_lock_comparison.py`

**Interfaces:**
- CLI adds required/defaulted `--run-profile`.
- Legacy `--model` and `--max-turns` default to `None`.
- Lock stores Run Profile identity, subprofile digests, mode/frozen/counted.

- [ ] **Step 1: Write RED admission tests**

Test Pilot/Formal override rejection before `ensure_image`, Smoke override
non-counted behavior, `.env` model selection removal, and lock/runtime equality.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/config/test_eval_run_profile.py tests/config/test_resolver.py tests/experiments/test_lock_comparison.py`

Expected: eval still defaults raw model/turn policy.

- [ ] **Step 3: Change CLI precedence**

Resolve `--run-profile` first. Pass old flags as `RunOverrides`. Remove
`DFTWORLD_MODEL` as a selection input. Keep provider-specific secret variables
loaded from `.env`.

- [ ] **Step 4: Freeze new lock fields**

Add `run_profile_name`, `run_profile_digest`, `profile_digests`, `mode`,
`override_present`, `frozen`, and `counted`. Preserve old-reader defaults when
these fields are absent.

- [ ] **Step 5: Tighten pair comparison**

Require identical Run Profile/subprofile digests for counted pairs. Allow only
condition and Skill identity fields to differ.

- [ ] **Step 6: Verify GREEN and commit**

Run the RED suite plus ablation pair tests.

Expected: PASS.

```bash
git add eval.py dftworld_bench/config/resolver.py \
  infra/schemas/resolved-run-lock.schema.json tests/config/test_eval_run_profile.py \
  tests/config/test_resolver.py tests/experiments/test_lock_comparison.py \
  tests/ablation/test_validate_pilot_pair.py
git commit -m "feat(eval): admit runs through frozen run profiles"
```

---

### Task 6: Migration, Forward Test, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: configuration and CLI tests as required by migration.

This task does not create `experiments/skill-ablation-v2/protocol.yaml`; that
file remains owned by the separately authorized Task 12 release workflow.

- [ ] **Step 1: Update operator examples**

Document `.env` as values only and replace raw Formal model/turn examples with
`--run-profile`. Document that compatibility flags make Smoke non-counted.

- [ ] **Step 2: Run config doctor**

With fake env values, run resolve/doctor for local Smoke, HPC Pilot, and HPC
Formal. Assert JSON contains no secret value and all digests are stable.

- [ ] **Step 3: Run deterministic forward tests**

Use ProcessDriver and a fake provider to execute one local Case and one HPC
Case. Assert actual provider/retry/turn inputs equal the lock and NS/WS diff is
Skill-only.

- [ ] **Step 4: Run full suite outside restricted sandbox**

Run: `.venv/bin/pytest -q -p no:cacheprovider tests`

Expected baseline: at least 1201 PASS, 0 FAIL, with only the existing 9 SKIP
plus newly added passing tests.

- [ ] **Step 5: Run activation**

Run simulated activation through the supported module/entry point. Expected:
D0–D10 PASS, D11/D12 NOT_RUN, Formal disabled.

- [ ] **Step 6: Source and secret audit**

Assert Cases contain no model/API/turn fields, Candidate env contains no API
names or values, Formal code has no raw model/turn default, and docs contain no
real credentials.

- [ ] **Step 7: Commit docs/migration tests**

```bash
git add README.md .env.example tests infra/config
git commit -m "docs(config): migrate operators to unified run profiles"
```

---

## Completion Definition

- one Run Profile selects all policy dimensions;
- model/API/turn edits affect actual runtime and lock together;
- secrets remain trusted and non-serializable;
- Pilot/Formal overrides are impossible;
- Smoke overrides are explicitly non-counted;
- config CLI resolves, validates, and diffs profiles;
- pair validation permits only Skill treatment difference;
- current records remain readable;
- full suite and simulated activation pass.
