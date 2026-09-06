# Single Run Config Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Replace split CLI/profile authority with one typed Run Config while preserving `eval.py CASE [--skills]`.

**Architecture:** Load and validate one YAML into immutable secret-free types, resolve secrets separately, route Agent budget by execution class, and inject the resolved values into provider, retry client, Harness, lock, and pair validator.

**Tech Stack:** Python 3.12+, Pydantic 2, PyYAML, pytest, pagentv4.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-08-23-single-run-config-design.md`.
- Skill is the only counted treatment.
- Routing is by execution class, not Case ID.
- Secret values never serialize or reach Candidate.
- Pilot/Formal overrides fail before side effects.
- Every behavior change follows RED→GREEN.

### Task 1: Typed Run Config

**Files:**
- Create: `infra/runs/skill-ablation-v2.yaml`
- Create: `dftworld_bench/config/run_config.py`
- Create: `tests/config/test_run_config.py`
- Modify: `pyproject.toml`

- [ ] Write RED tests for exact schema, unknown fields, invalid budgets, literal
  secrets, missing execution classes, treatment mismatch, canonical digest, and
  local/HPC budget selection.
- [ ] Run the test and verify feature-missing failures.
- [ ] Implement frozen Pydantic models and `load_run_config(path)`.
- [ ] Add the approved default YAML and explicit Pydantic dependency.
- [ ] Verify tests and commit `feat(config): add typed single run config`.

### Task 2: Secret-Free Config CLI

**Files:**
- Create: `dftworld_bench/config/cli.py`
- Create: `tests/config/test_config_cli.py`
- Modify: `pyproject.toml`

- [ ] Write RED tests for resolve, doctor, diff, missing env, unsupported
  provider, and recursive secret absence.
- [ ] Implement `dftworld-config` with secret-free JSON and typed exit status.
- [ ] Verify tests and commit `feat(config): add run config inspection CLI`.

### Task 3: Runtime Wiring

**Files:**
- Modify: `dftworld_bench/agents.py`
- Modify: `dftworld_bench/core/model_transport.py`
- Modify: `eval.py`
- Create: `tests/config/test_run_config_wiring.py`
- Modify: `tests/core/test_model_transport.py`

- [ ] Write RED tests that capture actual pagent constructor values, retry
  policy, Agent turns, ledger turns, and Candidate env.
- [ ] Resolve endpoint/key in trusted code and explicitly construct provider.
- [ ] Inject API retry/timeout values into `RetryingModelClient`.
- [ ] Inject one execution-class budget into Agent/Harness/ledger.
- [ ] Verify secret-isolation tests and commit
  `refactor(runtime): consume resolved run config`.

### Task 4: Eval UX and Lock Identity

**Files:**
- Modify: `eval.py`
- Modify: `dftworld_bench/config/resolver.py`
- Modify: `infra/schemas/resolved-run-lock.schema.json`
- Create: `tests/config/test_eval_run_config.py`
- Modify: `tests/experiments/test_lock_comparison.py`

- [ ] Write RED tests for unchanged NS/WS commands, automatic treatment,
  execution-class budget, conflicting condition, counted override rejection,
  uncounted Smoke override, and lock/runtime equality.
- [ ] Default `--run-config` to the approved YAML; remove model selection from
  `DFTWORLD_MODEL` for counted runs.
- [ ] Store Run Config digest and resolved policy in lock.
- [ ] Tighten pair comparison to Skill-only differences.
- [ ] Verify and commit `feat(eval): admit runs through single run config`.

### Task 5: Migration and Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: affected config and forward tests.

- [ ] Document values-only `.env`, old short NS/WS commands, optional
  `--run-config`, and uncounted override syntax.
- [ ] Run config, runtime wiring, pair, workspace-isolation, and forward tests.
- [ ] Run full suite outside restricted sandbox; require no failures.
- [ ] Run simulated activation; require D0–D10 PASS and D11/D12 NOT_RUN.
- [ ] Audit Cases and Candidate env for forbidden policy/secrets.
- [ ] Commit `docs(config): migrate eval to single run config`.

## Completion Definition

- one Run Config is the sole counted policy authority;
- existing NS/WS commands remain short;
- HPC Cases receive long turns automatically;
- runtime and lock values match;
- secrets remain trusted;
- full suite and activation pass.
