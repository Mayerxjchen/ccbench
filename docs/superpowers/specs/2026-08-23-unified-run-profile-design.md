# Unified Run Profile Configuration Design

**Status:** Superseded by `2026-08-23-single-run-config-design.md`
**Date:** 2026-08-23
**Scope:** DFTWorld model, API, Agent budget, experiment, runtime, and site configuration

## Goal

Make one named Run Profile the sole policy-selection entry for every DFTWorld
run. Resolve all referenced profiles before Candidate startup, inject secret
bytes only in the Trusted Harness, and freeze the resolved identities and
budgets into the Run Lock.

## Invariants

1. Cases never configure model, provider, endpoint, credential, retry, turns,
   cost, Skill treatment, runtime implementation, or site.
2. Environment variables contain secret/endpoint values only; they do not
   select model, turns, experiment, or site.
3. Pilot and Formal accept no policy override from CLI.
4. Smoke and Discovery may override model or turns, but their lock records
   `override_present=true`, `frozen=false`, and `counted=false`.
5. NS and WS resolve the same Run Profile and differ only in Skill treatment.
6. Runtime code consumes exactly the values stored in the resolved lock.
7. API credentials and endpoint values never enter Candidate env, public lock,
   logs, or result JSON.
8. Missing profile, secret, endpoint, model identity, or unsupported provider
   fails before Candidate startup.

## Configuration Graph

```text
run-profiles.toml
  -> agent-profiles.toml
  -> model-profiles.toml
  -> api-profiles.toml
  -> experiment-profiles.toml
  -> runtime-profiles.toml
  -> site-profiles.toml (optional)
  -> EnvSecretProvider (secret bytes only)
  -> ResolvedRunConfig
  -> ResolvedRunLock + runtime dependencies
```

## Run Profiles

```toml
[run_profiles.local-smoke-deepseek]
mode = "smoke"
agent = "local-standard"
model = "deepseek-chat"
api = "deepseek-default"
experiment = "default"
runtime = "local-sandbox"

[run_profiles.pilot-hpc-deepseek-v4]
mode = "pilot"
agent = "pilot-infra"
model = "deepseek-v4-pro"
api = "deepseek-default"
experiment = "pilot"
runtime = "hpc-controller"
site = "<site-alias>"

[run_profiles.formal-hpc-deepseek-v4]
mode = "formal"
agent = "formal-long"
model = "deepseek-v4-pro"
api = "deepseek-default"
experiment = "formal"
runtime = "hpc-controller"
site = "<site-alias>"
```

Run Profile fields are references, not duplicated policy. Profile digests bind
the referenced contents, so changing turns or retry policy changes the final
resolved identity.

## Model Profiles

```toml
[models.deepseek-v4-pro]
provider = "deepseek"
model_id = "deepseek-v4-pro"
deployment_id = "default"
identity_strength = "alias-only"
```

Supported providers in v1 match the installed pagent provider classes:
`deepseek`, `openai`, `kimi`, `longcat`, `mimo`, `ollama`, `vllm`, and
`sglang`. A configured but unsupported provider is rejected by `doctor` and
run admission.

## API Profiles and Secrets

```toml
[api.deepseek-default]
provider = "deepseek"
endpoint_env = "DEEPSEEK_BASE_URL"
credential_env = "DEEPSEEK_API_KEY"
max_attempts = 4
retry_base_delay_sec = 1.0
retry_max_delay_sec = 30.0
request_timeout_sec = 180.0
input_usd_micros_per_million_tokens = 0
output_usd_micros_per_million_tokens = 0
```

`.env` contains values only:

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

The Trusted Harness resolves both variables. It passes the revealed values
directly to the pagent provider constructor and passes the retry policy to
`RetryingModelClient`. The public lock stores only environment variable names,
profile digest, provider identity, and retry/cost policy.

## Resolved Run Configuration

```python
@dataclass(frozen=True)
class ResolvedRunConfig:
    name: str
    mode: Literal["smoke", "discovery", "pilot", "formal"]
    model: ResolvedModelConfig
    api: ResolvedApiConfig
    agent: ResolvedAgentConfig
    experiment: dict
    runtime: dict
    site: dict | None
    profile_digests: dict[str, str]
    override_present: bool
    frozen: bool
    counted: bool
```

Secret values are deliberately absent from this serializable object. A
separate trusted `ResolvedApiSecrets` object contains `SecretValue` instances
and exists only long enough to create the provider.

## CLI

Primary entry:

```text
eval.py CASE --run-profile pilot-hpc-deepseek-v4 --condition no-skill
```

Compatibility options `--model` and `--max-turns` default to `None`. They are
accepted only when the selected profile mode is `smoke` or `discovery`.
Pilot/Formal rejects either option before image resolution or Candidate start.

Configuration inspection:

```text
dftworld-config resolve PROFILE
dftworld-config doctor PROFILE
dftworld-config diff PROFILE_A PROFILE_B
```

`resolve` emits secret-free JSON. `doctor` checks references, schemas,
provider support, required environment variables, endpoint form, and site
availability without starting a Candidate. `diff` reports exact resolved
policy differences.

## Runtime Wiring

- `PagentAdapter` receives resolved model/API/Agent configuration rather than
  raw model and turn arguments.
- `make_provider()` receives provider, model ID, base URL, and a revealed key
  from trusted code.
- `RetryingModelClient` receives max attempts, backoff, and request timeout from
  the same resolved API profile.
- `HarnessSpec.max_turns`, Agent `max_turns`, budget ledger, and sampling digest
  all receive the same resolved Agent value.
- RunRecord stores Run Profile name/digest, subprofile digests, resolved model
  identity, and secret-free API policy.

## Formal Pair Rule

For a counted pair, Run Profile name and all subprofile digests are identical.
The mechanical pair validator allows only condition/Skill identity fields to
differ. CLI overrides, alias changes, missing endpoint identity, or price/retry
changes invalidate the comparison.

## Compatibility and Migration

1. Add new schemas and profiles without changing runtime.
2. Add resolver and configuration CLI.
3. Wire Agent/model/API runtime dependencies.
4. Change eval CLI to Run Profile selection.
5. Keep old flags for Smoke/Discovery with explicit non-counted status.
6. Update protocol/tests/docs and remove `DFTWORLD_MODEL` selection.

Old RunRecords remain readable. No historical lock is rewritten.

## Non-Goals

- storing secrets in TOML;
- endpoint fallback during a run;
- automatic model substitution;
- allowing Case-specific profiles;
- dynamic price lookup from the internet;
- adding new model providers beyond installed pagent support;
- changing HPC SiteProfile or scientific thresholds.

## Completion Criteria

- editing a referenced profile changes the resolved digest and runtime behavior;
- editing `model-profiles.toml` changes the actual provider/model used;
- editing `api-profiles.toml` changes actual retry/timeout behavior;
- Agent profile turns equal CLI-visible, lock, ledger, and runner turns;
- Pilot/Formal policy overrides fail before Candidate startup;
- Smoke overrides are visibly non-counted;
- secrets never serialize or reach Candidate;
- config doctor catches missing/unsupported configuration;
- NS/WS pair validation proves Skill is the only difference;
- full suite and forward configuration tests pass.
