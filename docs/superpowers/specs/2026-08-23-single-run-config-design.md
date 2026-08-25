# Single Run Config Design

**Status:** Approved
**Date:** 2026-08-23

## Goal

Use one versioned YAML file as the complete shared configuration for a
benchmark campaign while preserving the familiar commands:

```bash
uv run python eval.py CASE            # NS
uv run python eval.py CASE --skills   # WS
```

Skill availability is the only treatment. Model, API policy, budgets, runtime,
site, Case bytes, and seed are shared within every NS/WS pair.

## Configuration

The default counted-run configuration is
`infra/runs/skill-ablation-v2.yaml`. It contains model identity, secret env
names, API retry/timeout policy, and Agent budgets selected by execution class.

```yaml
schema_version: 1
run_config_id: skill-ablation-v2
mode: pilot
model:
  provider: deepseek
  model_id: deepseek-v4-pro
  deployment_id: default
  identity_strength: alias-only
api:
  endpoint_env: DEEPSEEK_BASE_URL
  credential_env: DEEPSEEK_API_KEY
  max_attempts: 4
  retry_base_delay_sec: 1.0
  retry_max_delay_sec: 30.0
  request_timeout_sec: 180.0
  input_usd_micros_per_million_tokens: 0
  output_usd_micros_per_million_tokens: 0
agent_by_execution_class:
  local_sandbox:
    max_model_turns: 64
    max_total_tokens: 10000000
    active_walltime_sec: 7200
    scheduler_wait_walltime_sec: 0
  hpc_controller:
    max_model_turns: 512
    max_total_tokens: 100000000
    active_walltime_sec: 86400
    scheduler_wait_walltime_sec: 604800
treatments:
  no-skill: {skills_enabled: false}
  with-skill: {skills_enabled: true}
```

Routing uses `execution.class`, never Case ID. A higher turn count is a ceiling
for long scientific work, not an experimental condition.

## Typed and Trusted Resolution

Pydantic models reject unknown fields, invalid values, unsupported providers,
missing execution classes, and literal secrets. Pydantic Settings or the
existing `EnvSecretProvider` resolves only values named by `endpoint_env` and
`credential_env`. Environment variables cannot select model, turns, mode, or
site.

The Trusted Harness explicitly passes endpoint and key to the pagent provider.
It passes retry/timeout policy to `RetryingModelClient` and the resolved turn
ceiling to Agent, Harness, ledger, and lock. Candidate receives none of the API
configuration or secret values.

## CLI and Override Policy

`eval.py` loads the default config automatically. `--run-config PATH` selects a
different versioned file for maintenance or a separately frozen campaign.

`--skills` selects `with-skill`; absence selects `no-skill`. An explicit
`--condition` may be retained, but disagreement with `--skills` is rejected.

Legacy `--model` and `--max-turns` are permitted only with `--uncounted-smoke`.
Such a run records `override_present=true`, `frozen=false`, and `counted=false`.
Pilot/Formal rejects overrides before Candidate startup.

## Identity

The resolver writes canonical, secret-free JSON and SHA-256. Run Lock stores:

- Run Config path, ID, digest, schema, and mode;
- resolved model/provider/deployment identity;
- secret environment variable names and API policy;
- selected execution-class budget;
- treatment name and Skill bundle identity;
- `override_present`, `frozen`, and `counted`.

The pair validator requires identical Run Config digest and resolved fields;
only treatment/Skill fields may differ.

## Inspection

```text
dftworld-config resolve [--run-config PATH] [--execution-class CLASS]
dftworld-config doctor  [--run-config PATH]
dftworld-config diff A B
```

Output is secret-free. Doctor checks schema, provider support, env presence,
endpoint syntax, execution-class coverage, and internal consistency without
starting Candidate or HPC.

## Compatibility

Old records remain readable. Existing profile TOMLs remain temporarily for
SiteProfile/runtime and historical readers, but model/API/turn selection for
new evals comes only from Run Config. `DFTWORLD_MODEL` ceases to select a
counted run.

## Completion Criteria

- old NS/WS commands work without repeating model or turns;
- 034/042 automatically receive the HPC long budget;
- actual provider, retry client, Agent, ledger, and lock use the same values;
- secrets never serialize or enter Candidate;
- counted overrides are impossible;
- NS/WS mechanical diff is Skill-only;
- full tests and simulated activation pass.
