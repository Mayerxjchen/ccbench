# Benchmark Infra v2 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the 001–042 benchmark into a durable, case-agnostic,
centrally configured infrastructure in which long scientific Agent runs are
recoverable and API/HPC/Verifier failures cannot contaminate scientific scores.

**Architecture:** A trusted resolver freezes Agent/API/Experiment/runtime
profiles into `resolved-run-lock/v2`; a durable Coordinator records hash-chained
events and mediates model I/O, Candidate tools, and external HPC waits. Execution
is selected only by `execution.class` and capabilities, with a secured common
Gateway, source-safe submission collection, independent non-root Verifier, and
RunRecord v2 binding every identity and budget.

**Tech Stack:** Python 3.12+, TOML/YAML, JSON Schema 2020-12, pytest, Docker,
Slurm through `bench-hpc`, append-only JSONL, SHA-256.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-08-20-benchmark-infra-v2-design.md` at baseline commit `66cdcf7`.
- Production scope is exactly 001–042: 37 `local_sandbox` and five target `hpc_controller` cases (031–034, 042).
- Common Core and shared plugins must contain no Case-identity dispatch.
- Case manifests may describe science, runtime requirements, execution class,
  public inputs, submission structure, and Verifier contract only.
- Model/API secrets remain in the trusted Harness; the Candidate never makes a
  provider request.
- Formal runs have finite turn, request, token, cost, active-time, total-time,
  local-tool, API-retry, scheduler-wait, and remote-compute budgets.
- Formal runs permit no model or endpoint fallback and no CLI field override.
- API retry and external wait do not consume accepted model turns.
- Logical operation identity is mandatory; physical LLM exactly-once is never
  claimed when the provider lacks idempotency.
- Candidate destruction precedes fresh, networkless, non-root Verifier startup.
- Every task uses TDD, leaves `pytest tests` green, and commits only its own files.
- Do not run a real Agent, external API, Docker scientific workload, SSH, or
  Slurm job until the task explicitly reaches the separately authorized real-run gate.

---

## File Structure

New focused units:

```text
infra/config/                         versioned profile values
infra/schemas/                        profile + resolved-lock schemas
dftworld_bench/config/profiles.py     typed central profile loader
dftworld_bench/config/resolver.py     experiment construction/formal resolution
dftworld_bench/config/secrets.py      trusted env/key lookup, redacted identity
dftworld_bench/contracts/resolved_lock.py immutable lock + canonical digest
dftworld_bench/contracts/events.py    hash-chained event contract
dftworld_bench/core/event_store.py    append-only event/checkpoint persistence
dftworld_bench/core/budgets.py        independent budget accounting
dftworld_bench/core/model_transport.py API attempts/retry/circuit breaker
dftworld_bench/core/coordinator.py    durable logical activity orchestration
dftworld_bench/core/submission_contract.py structural pre-Verifier gate
dftworld_bench/executors/base.py      executor protocol
dftworld_bench/executors/registry.py  execution-class registry
dftworld_bench/executors/local.py     Local executor
dftworld_bench/executors/hpc.py       generic HPC executor
dftworld_bench/runtime/registry.py    requirement -> runtime resolution
dftworld_bench/runtime/qualify.py     image/tool qualification
dftworld_bench/hpc/http_server.py     HTTP binding for the common Gateway
dftworld_bench/hpc/audit.py           job/operation audit ledger
scripts/infra/migrate_case_contracts.py deterministic 001–042 migration
scripts/infra/qualify_runtimes.py      preflight CLI
scripts/infra/activate_v2.py           final fail-closed acceptance dashboard
```

Existing large files remain compatibility facades: `eval.py` delegates to the
new resolver/coordinator/executor stack, and `dftworld_bench/agents.py` retains
provider glue until the final cleanup task.

---

## Pre-Implementation Gate: Establish an Authorized Baseline

The current main worktree contains uncommitted changes that overlap this plan
(`eval.py`, Agent/Harness/Verifier/Gateway files, 031–033 instructions/tests,
runtime build files and schemas) plus an untracked 042 Case and unrelated user
data. Implementation must not start from an ambiguous mixture.

Before Task 1:

1. Run `git status --short` and classify every changed path as:
   `infra-baseline`, `042-case-baseline`, `diagnostic-run-data`, or
   `unrelated-user-data`.
2. With explicit user authorization, create one baseline commit containing
   only the first two classes. Never stage workspaces, GO–water source data,
   ai2kit source trees, scratch scripts, or experiment run bytes by directory
   glob.
3. Confirm `git show --stat --oneline HEAD` contains exactly the authorized
   baseline paths.
4. Use the `using-git-worktrees` skill to create an isolated implementation
   worktree from that baseline commit.
5. Confirm the implementation worktree is clean and contains the 042 Case
   contract before running Task 1.

If the user chooses not to baseline the overlapping changes, implementation is
blocked; do not recreate, discard, or overwrite them in a new worktree.

---

### Task 1: Case Contract Hard Boundary and Runtime Requirements

**Files:**
- Modify: `schemas/case.schema.json`
- Modify: `dftworld_bench/contracts/case.py`
- Modify: `schemas/dftworld-target.schema.json`
- Modify: `dftworld_bench/case_factory/dftworld_target.py`
- Test: `tests/contracts/test_case_contract.py`
- Test: `tests/case_factory/test_dftworld_task_manifest.py`

**Interfaces:**
- Produces: `RuntimeRequirement(name: str, implementation: str | None, version: str | None)`
- Produces: `CaseSpec.runtime_requirements: tuple[RuntimeRequirement, ...]`
- Produces: `CaseSpec.submission_contract: dict[str, Any]`
- Consumes later: Tasks 2, 5, 11, 12, 15, 16.

- [ ] **Step 1: Write failing schema-boundary tests**

```python
@pytest.mark.parametrize("field", [
    "provider", "model", "endpoint", "api_key", "retry",
    "request_timeout", "max_turns", "agent_walltime", "skills", "fallback",
])
def test_case_rejects_infra_owned_agent_fields(case_raw, field):
    case_raw[field] = "forbidden"
    with pytest.raises(CaseContractError, match="Infra-owned"):
        CaseSpec._from_raw(case_raw, Path("case"))

def test_case_declares_requirements_not_image(case_raw):
    case_raw["runtime"] = {
        "requirements": [
            {"name": "dpmp", "implementation": "deepmd-jax", "version": ">=0.2"}
        ]
    }
    spec = CaseSpec._from_raw(case_raw, Path("case"))
    assert spec.runtime_requirements[0].name == "dpmp"
    assert spec.candidate_image is None
```

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest -q tests/contracts/test_case_contract.py tests/case_factory/test_dftworld_task_manifest.py`  
Expected: FAIL because Infra-owned fields are not scanned and runtime requirements are absent.

- [ ] **Step 3: Add typed requirements and hard rejection**

```python
INFRA_OWNED_CASE_FIELDS = frozenset({
    "provider", "model", "endpoint", "api_key", "retry",
    "request_timeout", "max_turns", "agent_walltime", "skills", "fallback",
})

@dataclass(frozen=True)
class RuntimeRequirement:
    name: str
    implementation: str | None = None
    version: str | None = None

def _reject_infra_owned_fields(raw: dict[str, Any]) -> None:
    found = sorted(INFRA_OWNED_CASE_FIELDS & set(raw))
    agent = raw.get("agent") or {}
    found += [f"agent.{k}" for k in sorted(INFRA_OWNED_CASE_FIELDS & set(agent))]
    if found:
        raise CaseContractError(f"Infra-owned Case fields are forbidden: {found}")
```

Keep a schema-versioned compatibility reader for existing `[agent].timeout_sec`
only until Tasks 15–16 migrate all 42 manifests. Mark it in
`CaseSpec.legacy_agent_fields`; Formal admission rejects any non-empty value.

- [ ] **Step 4: Render runtime requirements deterministically**

Update the Case Factory target so `[runtime]` contains scientific requirements
and never a concrete Docker tag. Sort requirements by `(name, implementation,
version)` before rendering.

- [ ] **Step 5: Run GREEN tests and full contract suite**

Run: `.venv/bin/pytest -q tests/contracts tests/case_factory`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add schemas/case.schema.json schemas/dftworld-target.schema.json \
  dftworld_bench/contracts/case.py dftworld_bench/case_factory/dftworld_target.py \
  tests/contracts/test_case_contract.py tests/case_factory/test_dftworld_task_manifest.py
git commit -m "feat(infra): separate case science from agent configuration"
```

---

### Task 2: Central Profile Registry and Trusted Secret Lookup

**Files:**
- Create: `infra/config/agent-profiles.toml`
- Create: `infra/config/model-profiles.toml`
- Create: `infra/config/api-profiles.toml`
- Create: `infra/config/experiment-profiles.toml`
- Create: `infra/config/runtime-profiles.toml`
- Create: `infra/config/site-profiles.toml`
- Create: `infra/schemas/agent-profile.schema.json`
- Create: `dftworld_bench/config/__init__.py`
- Create: `dftworld_bench/config/profiles.py`
- Create: `dftworld_bench/config/secrets.py`
- Test: `tests/config/test_profiles.py`
- Test: `tests/config/test_secrets.py`

**Interfaces:**
- Produces: `ProfileRegistry.load(root: Path) -> ProfileRegistry`
- Produces: `ProfileRegistry.require(kind: str, name: str) -> dict[str, Any]`
- Produces: `EnvSecretProvider.get(profile_id: str) -> SecretValue`
- Secret values expose `reveal()` only inside model transport.

- [ ] **Step 1: Write deterministic-profile and redaction tests**

```python
def test_profile_digest_ignores_toml_key_order(tmp_path):
    a = ProfileRegistry.from_mapping(PROFILES_A)
    b = ProfileRegistry.from_mapping(PROFILES_B_REORDERED)
    assert a.digest("agent", "formal-long") == b.digest("agent", "formal-long")

def test_secret_never_serializes(monkeypatch):
    monkeypatch.setenv("DFTWORLD_API_KEY_PRIMARY", "super-secret")
    value = EnvSecretProvider({"primary": "DFTWORLD_API_KEY_PRIMARY"}).get("primary")
    assert value.reveal() == "super-secret"
    assert "super-secret" not in repr(value)
    with pytest.raises(TypeError):
        json.dumps(value)
```

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest -q tests/config`  
Expected: import failure for the new config package.

- [ ] **Step 3: Implement canonical profile loading**

```python
@dataclass(frozen=True)
class ProfileRegistry:
    profiles: dict[str, dict[str, dict[str, Any]]]

    @classmethod
    def load(cls, root: Path) -> "ProfileRegistry":
        merged: dict[str, dict[str, dict[str, Any]]] = {}
        for path in sorted(root.glob("*-profiles.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for kind, values in data.items():
                merged.setdefault(kind, {}).update(values)
        return cls(merged)

    def digest(self, kind: str, name: str) -> str:
        return digest_bytes(canonical_json(self.require(kind, name)))
```

- [ ] **Step 4: Add exact standard profiles**

Encode these values from the approved design:

```toml
[agents.pilot-infra]
max_model_turns = 128
max_total_tokens = 25000000
agent_active_walltime_sec = 7200
scheduler_wait_walltime_sec = 172800

[agents.discovery-long]
max_model_turns = 256
max_total_tokens = 50000000
agent_active_walltime_sec = 43200
scheduler_wait_walltime_sec = 604800

[agents.formal-long]
max_model_turns = 512
max_total_tokens = 100000000
agent_active_walltime_sec = 86400
scheduler_wait_walltime_sec = 604800

[agents.local-standard]
max_model_turns = 64
max_total_tokens = 10000000
agent_active_walltime_sec = 7200
scheduler_wait_walltime_sec = 0
```

Use `endpoint_env` and `credential_env` names in API profiles; never commit an
endpoint token or API key.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest -q tests/config tests/test_package_imports.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infra/config infra/schemas dftworld_bench/config tests/config
git commit -m "feat(infra): add centralized profile and secret registry"
```

---

### Task 3: Resolved Run Lock v2 and Formal Freeze Boundary

**Files:**
- Create: `infra/schemas/resolved-run-lock.schema.json`
- Create: `dftworld_bench/contracts/resolved_lock.py`
- Create: `dftworld_bench/config/resolver.py`
- Modify: `eval.py`
- Test: `tests/contracts/test_resolved_lock.py`
- Test: `tests/config/test_resolver.py`
- Test: `tests/hpc/test_eval_run_identity.py`

**Interfaces:**
- Produces: `ResolvedRunLock(payload: dict[str, Any], digest: str)`
- Produces: `construct_experiment(selection, registry) -> FrozenExperiment`
- Produces: `resolve_formal(experiment, case, run_id, replicate) -> ResolvedRunLock`
- Produces failure: `FrozenExperimentOverrideError`.

- [ ] **Step 1: Write RED tests for lock completeness and override rejection**

```python
def test_formal_override_is_rejected(frozen_experiment, case):
    with pytest.raises(FrozenExperimentOverrideError, match="model"):
        resolve_formal(frozen_experiment, case, "run-1", 1, overrides={"model": "x"})

def test_lock_is_write_once_and_secret_free(tmp_path, complete_payload):
    lock = ResolvedRunLock.create(complete_payload)
    lock.write_once(tmp_path / "resolved-run-lock.json")
    assert "secret" not in json.dumps(lock.to_dict()).lower()
    with pytest.raises(FileExistsError):
        lock.write_once(tmp_path / "resolved-run-lock.json")
```

- [ ] **Step 2: Run RED tests**

Run: `.venv/bin/pytest -q tests/contracts/test_resolved_lock.py tests/config/test_resolver.py`  
Expected: import failure.

- [ ] **Step 3: Implement canonical immutable lock**

```python
@dataclass(frozen=True)
class ResolvedRunLock:
    payload: dict[str, Any]
    digest: str

    @classmethod
    def create(cls, payload: dict[str, Any]) -> "ResolvedRunLock":
        validate_json(payload, RESOLVED_LOCK_SCHEMA)
        canonical = canonical_json(payload)
        return cls(json.loads(canonical), digest_bytes(canonical.encode()))

    def write_once(self, path: Path) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({**self.payload, "lock_digest": self.digest}, handle,
                      sort_keys=True, indent=2)
            handle.write("\n")
```

The JSON schema requires the nine approved groups: `case`, `experiment`,
`agent`, `api`, `candidate_runtime`, `hpc`, `verifier`, `infra`, `budgets`.
The Agent group requires provider, model, deployment/version when exposed,
`identity_strength`, engine, prompt, sampling, context, Skill and complete tool
surface digests. For remote providers, per-response provider version and
response-metadata digests are stored later in RunRecord `model_attempts[]` and
the lock never upgrades an alias-only identity claim.

- [ ] **Step 4: Split CLI construction from Formal execution**

Add subcommands:

```text
eval.py experiment-freeze --template NAME --output experiments/NAME/manifest.json
eval.py run --experiment NAME --case CASE --replicate N
```

For `run`, reject model/API/budget/runtime/site/skill overrides before loading a
provider or Docker client. The CLI exits 2 with the machine-readable code
`FROZEN_EXPERIMENT_OVERRIDE_REJECTED` and the rejected field names.

- [ ] **Step 5: Run GREEN tests**

Run: `.venv/bin/pytest -q tests/contracts/test_resolved_lock.py tests/config/test_resolver.py tests/hpc/test_eval_run_identity.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infra/schemas/resolved-run-lock.schema.json \
  dftworld_bench/contracts/resolved_lock.py dftworld_bench/config/resolver.py \
  eval.py tests/contracts/test_resolved_lock.py tests/config/test_resolver.py \
  tests/hpc/test_eval_run_identity.py
git commit -m "feat(infra): freeze complete resolved run identity"
```

---

### Task 4: Treatment-Only Lock Comparator

**Files:**
- Create: `dftworld_bench/experiments/comparison.py`
- Modify: `dftworld_bench/experiments/ablation.py`
- Test: `tests/experiments/test_lock_comparison.py`
- Modify: `scripts/ablation/validate_pilot_pair.py`
- Test: `tests/ablation/test_validate_pilot_pair.py`

**Interfaces:**
- Produces: `compare_lock(left, right, treatment) -> ComparisonDiff`
- `ComparisonDiff` fields: `allowed_differences`, `unexpected_differences`, `valid`.

- [ ] **Step 1: Write the treatment matrix tests**

```python
def test_skill_ablation_allows_only_skill_fields(ns_lock, ws_lock):
    diff = compare_lock(ns_lock, ws_lock, "skill_availability")
    assert diff.valid
    assert set(diff.allowed_differences) == {
        "experiment.condition_id", "agent.skill_bundle_digest"
    }

@pytest.mark.parametrize("path", [
    "agent.model_id", "agent.tool_help_digest", "api.api_profile_digest",
    "budgets.max_model_turns", "experiment.max_concurrent_runs",
    "hpc.site_profile_digest", "verifier.runtime_digest",
])
def test_skill_ablation_rejects_confounds(ns_lock, ws_lock, path):
    mutate_path(ws_lock, path)
    assert path in compare_lock(ns_lock, ws_lock, "skill_availability").unexpected_differences
```

- [ ] **Step 2: Implement recursive canonical diff**

```python
TREATMENTS = {
    "skill_availability": frozenset({
        "experiment.condition_id", "agent.skill_bundle_digest"
    }),
    "model_identity": frozenset({
        "agent.provider", "agent.model_id", "agent.deployment_id",
        "agent.provider_model_version", "agent.identity_strength",
        "agent.model_identity_digest",
    }),
}
```

- [ ] **Step 3: Replace the legacy `_PAIRED_FIELDS` comparison**

Pilot validation loads both resolved locks and fails before checking results if
`unexpected_differences` is non-empty.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/experiments/test_lock_comparison.py tests/ablation/test_validate_pilot_pair.py`  
Expected: PASS.

```bash
git add dftworld_bench/experiments/comparison.py dftworld_bench/experiments/ablation.py \
  scripts/ablation/validate_pilot_pair.py tests/experiments/test_lock_comparison.py \
  tests/ablation/test_validate_pilot_pair.py
git commit -m "feat(infra): enforce declared-treatment-only comparisons"
```

---

### Task 5: Structural Submission Contract and Failure Taxonomy

**Files:**
- Modify: `schemas/case.schema.json`
- Modify: `schemas/result.schema.json`
- Modify: `dftworld_bench/contracts/result.py`
- Create: `dftworld_bench/core/submission_contract.py`
- Modify: `dftworld_bench/core/harness.py`
- Test: `tests/core/test_submission_contract.py`
- Test: `tests/contracts/test_result_contract.py`
- Test: `tests/core/test_harness.py`

**Interfaces:**
- Produces: `validate_submission(root, contract) -> list[StructuralError]`
- Adds Agent code: `AGENT_BUDGET_EXHAUSTED`.
- Adds Infra codes: `API_TRANSIENT_EXHAUSTED`, `API_RATE_LIMIT_EXHAUSTED`,
  `API_AUTH_CONFIG`, `API_QUOTA`, `API_CONFIGURATION`.

- [ ] **Step 1: Write classification RED tests**

```python
def test_missing_required_artifact_is_agent_failure(tmp_path):
    errors = validate_submission(tmp_path, {"required": [{"path": "model.pb", "type": "file"}]})
    assert errors[0].code == "MISSING_REQUIRED_PATH"

def test_harness_stops_before_verifier_on_structural_failure(harness_fixture):
    result = harness_fixture.run_missing_required_file()
    assert result.result_class is ResultClass.AGENT_FAILURE
    assert result.failure_code is FailureCode.INVALID_SUBMISSION
    assert "verifier_start" not in harness_fixture.harness.events
```

- [ ] **Step 2: Implement non-executing structural validation**

Validate normalized relative paths, regular-file/directory type, per-file size,
and JSON schema where explicitly declared. Do not import Python, unpickle, or
judge numeric science.

- [ ] **Step 3: Insert the gate before quarantine/Verifier**

The order becomes collection → structural validation → quarantine/seal →
Verifier. Unsafe filesystem nodes are still rejected at source collection.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_submission_contract.py tests/contracts/test_result_contract.py tests/core/test_harness.py`  
Expected: PASS.

```bash
git add schemas/case.schema.json schemas/result.schema.json \
  dftworld_bench/contracts/result.py dftworld_bench/core/submission_contract.py \
  dftworld_bench/core/harness.py tests/core/test_submission_contract.py \
  tests/contracts/test_result_contract.py tests/core/test_harness.py
git commit -m "feat(infra): separate invalid submissions from scientific failure"
```

---

### Task 6: Hash-Chained Event and Checkpoint Store

**Files:**
- Create: `schemas/run-event.schema.json`
- Create: `dftworld_bench/contracts/events.py`
- Create: `dftworld_bench/core/event_store.py`
- Test: `tests/contracts/test_events.py`
- Test: `tests/core/test_event_store.py`

**Interfaces:**
- Produces: `EventStore.append(operation_id, kind, payload) -> RunEvent`
- Produces: `EventStore.validate_chain() -> None`
- Produces: `EventStore.write_checkpoint(state) -> Checkpoint`
- Produces: `EventStore.load_checkpoint(lock_digest) -> Checkpoint | None`.

- [ ] **Step 1: Write mutation/reorder/truncation RED tests**

```python
def test_event_chain_detects_reorder(store):
    store.append("MODEL-1", "attempt_started", {})
    store.append("MODEL-1", "attempt_succeeded", {"attempt": 1})
    swap_jsonl_lines(store.path, 0, 1)
    with pytest.raises(EventChainError):
        store.validate_chain()

def test_checkpoint_rejects_changed_lock(store):
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 4})
    with pytest.raises(CheckpointError, match="lock digest"):
        store.load_checkpoint("sha256:b")
```

- [ ] **Step 2: Implement canonical chained events**

```python
def next_event(previous: RunEvent | None, operation_id: str,
               kind: str, payload: dict[str, Any], at: str) -> RunEvent:
    seq = 1 if previous is None else previous.event_seq + 1
    prev = None if previous is None else previous.event_digest
    body = {"event_seq": seq, "event_id": f"evt-{seq:08d}",
            "operation_id": operation_id, "kind": kind,
            "previous_event_digest": prev, "timestamp": at, "payload": payload}
    return RunEvent(**body, event_digest=digest_bytes(canonical_json(body).encode()))
```

Use append + flush + fsync; never rewrite JSONL. Write checkpoints atomically to
a new content-addressed file and update only a small pointer with `os.replace`.

- [ ] **Step 3: Run tests and commit**

Run: `.venv/bin/pytest -q tests/contracts/test_events.py tests/core/test_event_store.py`  
Expected: PASS.

```bash
git add schemas/run-event.schema.json dftworld_bench/contracts/events.py \
  dftworld_bench/core/event_store.py tests/contracts/test_events.py \
  tests/core/test_event_store.py
git commit -m "feat(infra): add hash-chained durable run events"
```

---

### Task 7: Independent Budget Accounting

**Files:**
- Create: `dftworld_bench/core/budgets.py`
- Test: `tests/core/test_budgets.py`
- Modify: `dftworld_bench/core/harness.py`
- Test: `tests/core/test_harness.py`

**Interfaces:**
- Produces: `BudgetPolicy.from_lock(lock) -> BudgetPolicy`
- Produces: `BudgetLedger.charge(domain, amount, operation_id) -> None`
- Produces: `BudgetLedger.snapshot() -> dict[str, int]`
- Raises: `BudgetExceeded(domain, used, limit)`.

- [ ] **Step 1: Write RED domain-accounting tests**

```python
def test_api_retry_does_not_consume_turn(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("api_attempts", 1, "MODEL-1/A1")
    ledger.charge("api_retry_walltime_ms", 900, "MODEL-1/A1")
    assert ledger.used("model_turns") == 0

def test_external_wait_consumes_total_not_active(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("scheduler_wait_ms", 3_600_000, "JOB-1")
    ledger.charge("run_total_walltime_ms", 3_600_000, "JOB-1")
    assert ledger.used("agent_active_walltime_ms") == 0
```

- [ ] **Step 2: Implement finite ledgers**

Domains are model turns, logical requests, API attempts, tokens, USD microcost,
active time, total time, local tool time, API retry time, scheduler wait, jobs,
CPU-hours, GPU-hours, and storage-byte-hours. Reject missing Formal limits.

The Agent profile and resolved lock also require
`max_single_tool_walltime_sec`. A single Candidate tool invocation may not
consume the whole active/total budget while producing no Runner event.

- [ ] **Step 3: Integrate budget snapshots into Harness metadata**

Never infer accepted turns from tool calls. The model transport explicitly
charges a turn only when an accepted response is committed.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_budgets.py tests/core/test_harness.py`  
Expected: PASS.

```bash
git add dftworld_bench/core/budgets.py dftworld_bench/core/harness.py \
  tests/core/test_budgets.py tests/core/test_harness.py
git commit -m "feat(infra): enforce independent finite run budgets"
```

---

### Task 8: Trusted Model Transport, Retry Taxonomy, and Attempt Ledger

**Files:**
- Create: `dftworld_bench/core/model_transport.py`
- Modify: `dftworld_bench/agents.py`
- Test: `tests/core/test_model_transport.py`
- Test: `tests/test_workspace_isolation.py`

**Interfaces:**
- Produces: `RetryingModelClient.request(operation_id, request) -> AcceptedResponse`
- Produces: `ModelAttempt(state, provider_request_id, usage, metadata_digest)`
- Consumes: `EnvSecretProvider`, `BudgetLedger`, `EventStore`.

- [ ] **Step 1: Write retry and unknown-completion RED tests**

```python
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
async def test_transient_status_retries(status, fake_transport):
    fake_transport.responses = [HttpFailure(status), ModelResponse("ok")]
    result = await client(fake_transport, max_attempts=4).request("MODEL-1", REQUEST)
    assert result.text == "ok"
    assert [a.state for a in result.attempts] == ["known_failure", "success"]

async def test_lost_response_accepts_only_retry(fake_transport):
    fake_transport.responses = [TimeoutUnknown("req-1"), ModelResponse("second", "req-2")]
    result = await client(fake_transport).request("MODEL-1", REQUEST)
    assert result.accepted_attempt == 2
    assert result.attempts[0].state == "timeout_unknown"
```

- [ ] **Step 2: Implement one retry owner with full jitter**

```python
RETRYABLE_HTTP = frozenset({408, 429, 500, 502, 503, 504, 529})

def full_jitter(base: float, cap: float, retry_index: int, rng: Random) -> float:
    return rng.uniform(0.0, min(cap, base * (2 ** retry_index)))
```

Disable nested SDK retries or set them to one attempt. Honor `Retry-After` up to
the frozen max delay. Add a bounded circuit breaker that blocks new admissions
without switching endpoint.

- [ ] **Step 3: Keep model client outside Candidate**

Add a test that Candidate environment contains neither API credential nor
endpoint secret. The trusted Runner sends tool actions to `AgentAdapter`; the
Candidate returns tool results only.

- [ ] **Step 4: Normalize terminal API failures**

Map exhausted transient/rate-limit and fail-fast auth/quota/config errors to the
FailureCodes introduced in Task 5. Redact HTML bodies from public records.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_model_transport.py tests/test_workspace_isolation.py`  
Expected: PASS.

```bash
git add dftworld_bench/core/model_transport.py dftworld_bench/agents.py \
  tests/core/test_model_transport.py tests/test_workspace_isolation.py
git commit -m "feat(infra): mediate durable model requests outside candidate"
```

---

### Task 9: Durable Run Coordinator and External Wait

**Files:**
- Create: `dftworld_bench/core/coordinator.py`
- Modify: `dftworld_bench/core/harness.py`
- Modify: `eval.py`
- Test: `tests/core/test_coordinator.py`
- Test: `tests/core/test_harness.py`

**Interfaces:**
- Produces: `RunCoordinator.start(lock, case) -> BenchmarkResult`
- Produces: `RunCoordinator.resume(run_id) -> BenchmarkResult`
- Produces: `yield_external(job_ids, deadline) -> ExternalWait`.

- [ ] **Step 1: Write crash/resume RED tests at every side-effect boundary**

```python
@pytest.mark.parametrize("crash_after", [
    "model_response", "tool_result", "job_submit", "artifact_fetch",
    "submission_seal", "verifier_result",
])
def test_resume_commits_each_logical_effect_once(crash_after, coordinator_fixture):
    coordinator_fixture.crash_after(crash_after)
    with pytest.raises(SimulatedCrash):
        coordinator_fixture.start()
    result = coordinator_fixture.resume()
    assert result is not None
    coordinator_fixture.assert_one_accepted_transition_per_operation()
```

- [ ] **Step 2: Implement activity replay**

For an operation ID, inspect the validated event chain. Return a committed
result if present; otherwise execute the operation and append attempt/result
events before advancing.

- [ ] **Step 3: Implement Coordinator-owned HPC waiting**

```python
async def wait_external(self, job_ids: tuple[str, ...], deadline: float) -> dict[str, str]:
    self.events.append("WAIT", "external_wait_started", {"job_ids": job_ids})
    states = await self.hpc_waiter.wait(job_ids, deadline, self.budgets)
    self.events.append("WAIT", "external_wait_completed", {"states": states})
    self.checkpoint()
    return states
```

Reject model-driven `sleep` polling in Formal tool policy. Resume Agent only on
terminal/progress/deadline events.

Add a Coordinator-owned watchdog around every local Candidate tool operation.
Start the command in its own process group; on the frozen single-tool deadline,
send TERM, wait a bounded grace interval, then KILL the full process group.
Append `tool_attempt_timed_out` with the operation ID and return a structured
tool failure so the Agent may choose a bounded alternative. Repeated timeouts
consume the local-tool budget; only exhaustion becomes
`AGENT_FAILURE/AGENT_BUDGET_EXHAUSTED`. The regression must reproduce the
observed 033 failure using an unbounded root-recursive command (or an equivalent
fixture) and prove that the Agent/Coordinator receives control again without
waiting for the run-total deadline.

- [ ] **Step 4: Make `eval.py` a thin entrypoint**

It selects a frozen experiment and case, resolves the lock, then invokes the
Coordinator. Remove direct orchestration from `amain` incrementally while
retaining compatibility tests.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_coordinator.py tests/core/test_harness.py tests/hpc/test_eval_run_identity.py`  
Expected: PASS.

```bash
git add dftworld_bench/core/coordinator.py dftworld_bench/core/harness.py \
  eval.py tests/core/test_coordinator.py tests/core/test_harness.py \
  tests/hpc/test_eval_run_identity.py
git commit -m "feat(infra): add durable resumable run coordinator"
```

---

### Task 10: Secure Common Gateway, Job Ownership, and HPC Idempotency

**Files:**
- Create: `dftworld_bench/hpc/audit.py`
- Create: `dftworld_bench/hpc/http_server.py`
- Modify: `dftworld_bench/hpc/gateway.py`
- Modify: `dftworld_bench/hpc/gateway_runtime.py`
- Modify: `dftworld_bench/hpc/job.py`
- Modify: `dftworld_bench/hpc/adapters/base.py`
- Modify: `dftworld_bench/hpc/adapters/slurm.py`
- Test: `tests/hpc/test_gateway_security.py`
- Test: `tests/hpc/test_gateway.py`
- Test: `tests/hpc/test_slurm_adapter.py`

**Interfaces:**
- Produces: `Gateway.submit(..., operation_id) -> owned job`
- Produces: `GatewayAudit.append(event) -> digest`
- Adapter adds: `find_by_operation_id(run_id, operation_id) -> job_id | None`.

- [ ] **Step 1: Write direct-HTTP adversarial RED tests**

```python
@pytest.mark.parametrize("path", [
    "/etc/passwd", "/Users/example/.ssh/config", "/app/../secret", "../other-run",
])
def test_gateway_rejects_non_workspace_paths(gateway, token, valid_job_spec, path):
    valid_job_spec["inputs"] = [{"source": path, "destination": "input.dat"}]
    with pytest.raises(GatewayError, match="workspace"):
        gateway.submit(token, "run-1", valid_job_spec)

def test_token_cannot_touch_other_run_job(gateway):
    job = submit_for(gateway, "run-a", "JOB-1")
    with pytest.raises(GatewayError, match="not part of run"):
        gateway.status(token_for(gateway, "run-b"), "run-b", job["job_id"])
```

- [ ] **Step 2: Enforce canonical containment inside Gateway**

```python
def contained(root: Path, candidate: Path) -> Path:
    root_real = root.resolve(strict=True)
    value = candidate.resolve(strict=True)
    if value != root_real and root_real not in value.parents:
        raise GatewayError(f"path escapes run workspace: {candidate}")
    if candidate.is_symlink():
        raise GatewayError(f"symlink input rejected: {candidate}")
    return value
```

Do not pass unmatched absolute host paths through. Generate remote paths solely
from `<site-root>/<case>/<run>/<job>`.

- [ ] **Step 3: Add scheduler operation identity**

Before submit, call `find_by_operation_id`. Include operation ID in Slurm job
name/comment and remote marker. A duplicate returns the prior job ID.

- [ ] **Step 4: Replace MatClaw's thin trust boundary**

`scripts/matclaw_hpc_gateway.py` becomes a compatibility client/adapter behind
the common Gateway or is removed after Task 16. Candidate tokens can reach only
the common Gateway HTTP binding.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_gateway_security.py tests/hpc/test_gateway.py tests/hpc/test_gateway_runtime.py tests/hpc/test_slurm_adapter.py tests/test_matclaw_hpc_gateway.py`  
Expected: PASS.

```bash
git add dftworld_bench/hpc scripts/matclaw_hpc_gateway.py \
  tests/hpc/test_gateway_security.py tests/hpc/test_gateway.py \
  tests/hpc/test_gateway_runtime.py tests/hpc/test_slurm_adapter.py \
  tests/test_matclaw_hpc_gateway.py
git commit -m "fix(infra): enforce gateway containment and job ownership"
```

---

### Task 11: Case-Agnostic Executor Registry

**Files:**
- Create: `dftworld_bench/executors/__init__.py`
- Create: `dftworld_bench/executors/base.py`
- Create: `dftworld_bench/executors/registry.py`
- Create: `dftworld_bench/executors/local.py`
- Create: `dftworld_bench/executors/hpc.py`
- Modify: `dftworld_bench/core/coordinator.py`
- Modify: `eval.py`
- Test: `tests/executors/test_registry.py`
- Test: `tests/executors/test_no_identity_dispatch.py`

**Interfaces:**
- Produces: `Executor.prepare(context)`, `execute(context)`, `settle(context)`, `close(context)`.
- Produces: `ExecutorRegistry.resolve(execution_class) -> Executor`.

- [ ] **Step 1: Write dummy-case extension RED test**

```python
def test_new_hpc_case_needs_no_core_edit(registry, dummy_case):
    dummy_case.execution_class = "hpc_controller"
    executor = registry.resolve(dummy_case.execution_class)
    assert isinstance(executor, HpcExecutor)

def test_shared_runtime_sources_have_no_case_id_dispatch():
    forbidden = re.compile(r"(?:case(?:_id)?|task\.name)\s*==\s*['\"]\d{3}")
    for path in SHARED_RUNTIME_SOURCES:
        assert not forbidden.search(path.read_text()), path
```

- [ ] **Step 2: Implement protocol and registry**

```python
@runtime_checkable
class Executor(Protocol):
    async def prepare(self, context: ExecutionContext) -> None: ...
    async def execute(self, context: ExecutionContext) -> None: ...
    async def settle(self, context: ExecutionContext) -> None: ...
    async def close(self, context: ExecutionContext) -> None: ...

EXECUTORS = {
    "local_sandbox": LocalExecutor,
    "hpc_controller": HpcExecutor,
}
```

- [ ] **Step 3: Delete name-based runtime branches**

Remove `CONTROLLER_TASKS`, `_CONTROLLER_CASE`, and all `task.name in ...`
decisions. `execution_class` determines Gateway issuance and local GPU=0.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/executors tests/hpc/test_eval_run_identity.py tests/test_workspace_isolation.py`  
Expected: PASS and source scan finds no identity branch.

```bash
git add dftworld_bench/executors dftworld_bench/core/coordinator.py \
  dftworld_bench/agents.py eval.py tests/executors \
  tests/hpc/test_eval_run_identity.py tests/test_workspace_isolation.py
git commit -m "refactor(infra): dispatch execution only by contract"
```

---

### Task 12: Runtime Registry and Image/CLI Qualification

**Files:**
- Create: `dftworld_bench/runtime/__init__.py`
- Create: `dftworld_bench/runtime/registry.py`
- Create: `dftworld_bench/runtime/qualify.py`
- Create: `scripts/infra/qualify_runtimes.py`
- Modify: `base-env-build/build.sh`
- Modify: `base-env-build/matclaw-cips-controller/Dockerfile`
- Modify: `base-env-build/ai2kit-controller/Dockerfile`
- Modify: `base-env-build/deepmd-jax/Dockerfile`
- Modify: `dftworld_bench/hpc/__main__.py`
- Test: `tests/runtime/test_registry.py`
- Test: `tests/runtime/test_qualify.py`
- Test: `tests/hpc/test_client.py`

**Interfaces:**
- Produces: `RuntimeRegistry.resolve(requirements, execution_class) -> RuntimeSet`.
- `RuntimeSet`: Candidate, control, compute, Verifier digest identities.
- Produces: `qualify_runtime(runtime, runner) -> QualificationReport`.

- [ ] **Step 1: Write resolution and CLI RED tests**

```python
def test_dpmp_requirements_resolve_without_case_id(registry):
    resolved = registry.resolve([RuntimeRequirement("dpmp", "deepmd-jax", ">=0.2")],
                                "hpc_controller")
    assert resolved.compute.profile == "dpmp-jax-v1"
    assert resolved.control.profile == "bench-hpc-control-v1"

def test_unknown_cli_lists_commands(capsys):
    assert hpc_main(["not-a-command"]) == 2
    assert "capabilities" in capsys.readouterr().err
```

- [ ] **Step 2: Implement capability-based resolution**

Profiles list `provides`, version constraints, platform, and immutable image/SIF
digests. The resolver computes set coverage and fails on zero or ambiguous
matches.

- [ ] **Step 3: Qualify all four runtime identities**

Qualification checks image digest, platform, fixed non-root UID where relevant,
Python imports, `bench-hpc help`, every legal command, and absence of ssh/private
keys/site config in control images.

For GPU HPC runtimes, qualification also freezes the resolved scheduler GPU
identity. A site profile must distinguish full-card and MIG resources; an
ambiguous `gpu:1` request is not sufficient evidence. Fake-adapter tests assert
the resolved GRES/type, and the real-site canary records both `ReqTRES` and
`AllocTRES`. Formal admission fails if a full-card profile resolves to
`nvidia_a100_2g.20gb` or any other unexpected subtype.

- [ ] **Step 4: Repair controller images and CLI**

Install PyYAML into the interpreter used by `bench-hpc`, provide `help`, and
make unknown operation output self-describing. Build script accepts both root
and legacy Dockerfile layouts.

- [ ] **Step 5: Run non-Docker tests and syntax checks**

Run: `.venv/bin/pytest -q tests/runtime tests/hpc/test_client.py`  
Run: `bash -n base-env-build/build.sh`  
Expected: PASS.

- [ ] **Step 6: Authorized image qualification gate**

Only with explicit authorization, build/inspect the required images and run:

```bash
.venv/bin/python scripts/infra/qualify_runtimes.py --all --json
```

Expected: every production runtime `qualified=true`; no scientific workflow is
started.

- [ ] **Step 7: Commit**

```bash
git add dftworld_bench/runtime scripts/infra/qualify_runtimes.py \
  base-env-build/build.sh base-env-build/matclaw-cips-controller/Dockerfile \
  base-env-build/ai2kit-controller/Dockerfile base-env-build/deepmd-jax/Dockerfile \
  dftworld_bench/hpc/__main__.py tests/runtime tests/hpc/test_client.py
git commit -m "feat(infra): resolve and qualify runtime identities"
```

---

### Task 13: Source-Safe Collection and Non-Root Verifier Runtime

**Files:**
- Modify: `dftworld_bench/core/quarantine.py`
- Modify: `dftworld_bench/core/verifier.py`
- Modify: `tests/core/test_quarantine.py`
- Modify: `tests/core/test_verifier_runner.py`
- Modify: `tests/case_factory/test_runnable_local_draft.py`
- Modify verifier paths in:
  `012-ase-methane/tests/test_outputs.py`,
  `025-name2smi/tests/test_outputs.py`,
  `026-name2coord/tests/test_outputs.py`,
  `027-name2opt-so2/tests/test_outputs.py`,
  `028-name2vib-water/tests/test_outputs.py`,
  `029-name2gibbs-co2/tests/test_outputs.py`,
  `030-name2file-so2/tests/test_outputs.py`,
  `035-smiles2opt/tests/test_outputs.py`,
  `036-smiles2vib/tests/test_outputs.py`,
  `037-smiles2gibbs/tests/test_outputs.py`,
  `038-smiles2file/tests/test_outputs.py`,
  `039-react2enthalpy-methane/tests/test_outputs.py`,
  `040-react2gibbs-ammonia/tests/test_outputs.py`,
  `041-smiles2coord/tests/test_outputs.py`.

**Interfaces:**
- Collection rejects unsafe source nodes before byte copy.
- Verifier uses fixed UID `65532:65532` and `/opt/dftworld/venv/bin/python`.

- [ ] **Step 1: Write RED symlink tests against the collector**

```python
@pytest.mark.parametrize("broken", [False, True])
def test_collector_rejects_file_symlink(tmp_path, broken):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    target = workspace / "target" 
    if not broken: target.write_text("bytes")
    (workspace / "alias").symlink_to(target.name)
    with pytest.raises(QuarantineError, match="symlink"):
        collect_raw_submission(workspace, ".", tmp_path / "raw", True)
```

Also add permanent Layer-A regressions asserting all Docker volume sources are
absolute, `/solution` is mounted only into the fresh Verifier,
Verifier tmpfs comes from the resolved profile, and the 031 production
`test.sh` calls its scientific `verify()` entry rather than development pytest
retraining suites. The source-symlink fixture reproduces the 031 checkpoint
symlinks (`model.ckpt.meta/index/data`) that previously broke collection.

- [ ] **Step 2: Replace `shutil.copyfile` traversal with lstat-safe copying**

Reject unsafe directories while pruning `os.walk`; open regular files with
`O_NOFOLLOW` where available and verify `fstat` remains regular before copying.
Harness maps a Candidate-origin unsafe node to
`AGENT_FAILURE/INVALID_SUBMISSION`; an OS error after a source node has passed
validation remains `INFRA_INVALID/HARNESS_FAILURE`.

- [ ] **Step 3: Restore non-root Verifier isolation**

Restore `--user 65532:65532`. Runtime qualification must prove all test runners
and dependencies are accessible outside `/root` and `/app`.

- [ ] **Step 4: Migrate legacy `/app/.venv` test paths**

Use `VERIFIER_PYTHON=/opt/dftworld/venv/bin/python` injected by Harness. Tests
must never execute an interpreter shipped in the sealed submission.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_quarantine.py tests/core/test_verifier_runner.py tests/case_factory/test_runnable_local_draft.py`  
Expected: PASS.

```bash
git add dftworld_bench/core/quarantine.py dftworld_bench/core/verifier.py \
  tests/core/test_quarantine.py tests/core/test_verifier_runner.py \
  tests/case_factory/test_runnable_local_draft.py \
  012-ase-methane/tests/test_outputs.py 025-name2smi/tests/test_outputs.py \
  026-name2coord/tests/test_outputs.py 027-name2opt-so2/tests/test_outputs.py \
  028-name2vib-water/tests/test_outputs.py 029-name2gibbs-co2/tests/test_outputs.py \
  030-name2file-so2/tests/test_outputs.py 035-smiles2opt/tests/test_outputs.py \
  036-smiles2vib/tests/test_outputs.py 037-smiles2gibbs/tests/test_outputs.py \
  038-smiles2file/tests/test_outputs.py 039-react2enthalpy-methane/tests/test_outputs.py \
  040-react2gibbs-ammonia/tests/test_outputs.py 041-smiles2coord/tests/test_outputs.py
git commit -m "fix(infra): reject source symlinks and restore non-root verifier"
```

---

### Task 14: Monotonic Lifecycle, RunRecord v2, and Invalid Ledger

**Files:**
- Modify: `dftworld_bench/core/harness.py`
- Modify: `dftworld_bench/core/lifecycle.py`
- Modify: `dftworld_bench/contracts/run_record.py`
- Modify: `dftworld_bench/core/run_store.py`
- Modify: `schemas/run-record.schema.json`
- Create: `dftworld_bench/experiments/invalid_ledger.py`
- Test: `tests/core/test_lifecycle.py`
- Test: `tests/core/test_harness.py`
- Test: `tests/core/test_run_store.py`
- Test: `tests/contracts/test_run_record_v2.py`
- Test: `tests/experiments/test_invalid_ledger.py`

**Interfaces:**
- Produces: `RunRecordV2` with lock, events, attempts, budgets, runtime identities,
  `remote_jobs[]`, seal, Verifier and result.
- Reads v1 records for historical reporting but writes only v2.

- [ ] **Step 1: Write RED terminal and ledger tests**

```python
def test_record_has_exactly_one_terminal(record_payload):
    record_payload["lifecycle_events"] += [
        {"phase": "COMPLETED", "at": "t"},
        {"phase": "INVALID_INFRA", "at": "t2"},
    ]
    with pytest.raises(RunRecordError, match="one terminal"):
        RunRecordV2.from_dict(record_payload).validate()

def test_formal_infra_invalid_is_auto_ledgered(store, formal_record):
    store.create(formal_record)
    assert formal_record.run_id in InvalidRunLedger.load(store.ledger_path).run_ids
```

- [ ] **Step 2: Remove `record_write -> COMPLETED` mapping**

`record_write` is an action. Append exactly one terminal derived from Result
class, then validate the full lifecycle state machine and hash chain.

- [ ] **Step 3: Implement v2 schema and v1 reader**

Require model attempts, budgets, resolved lock digest, event root digest,
Candidate/control/compute/Verifier identities, real site identity, job audit,
submission seal, and normalized result.

- [ ] **Step 4: Add atomic append-only invalid ledger**

Create one immutable ledger entry per Formal `INFRA_INVALID` record. Aggregation
fails on an unledgered record, a missing record, or a duplicate conflicting entry.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/core/test_lifecycle.py tests/core/test_run_store.py tests/contracts/test_run_record_v2.py tests/experiments/test_invalid_ledger.py`  
Expected: PASS.

```bash
git add dftworld_bench/core/harness.py dftworld_bench/core/lifecycle.py \
  dftworld_bench/contracts/run_record.py dftworld_bench/core/run_store.py \
  dftworld_bench/experiments/invalid_ledger.py schemas/run-record.schema.json \
  tests/core/test_lifecycle.py tests/core/test_run_store.py \
  tests/contracts/test_run_record_v2.py tests/experiments/test_invalid_ledger.py \
  tests/core/test_harness.py
git commit -m "feat(infra): persist one-terminal run records with invalid ledger"
```

---

### Task 15: Migrate and Qualify All 37 Local Cases

**Files:**
- Create: `scripts/infra/migrate_case_contracts.py`
- Modify: `002-arithmetic/task.toml` through `030-name2file-so2/task.toml`, excluding already strict `009-cp2k-run`
- Modify: `035-smiles2opt/task.toml` through `041-smiles2coord/task.toml`
- Modify: `base-env-build/build.sh`
- Modify: `pyproject.toml`
- Create: `tests/contracts/test_all_case_contracts.py`
- Create: `tests/runtime/test_local_gold_matrix.py`

**Interfaces:**
- Script supports `--check` and `--write`; output is deterministic.
- Every Local Case resolves `local_sandbox`, no legacy Agent fields, and no
  concrete Infra image in its manifest.

- [ ] **Step 1: Write RED all-case contract test**

```python
LOCAL_IDS = set(range(1, 31)) | set(range(35, 42))

def test_all_local_cases_are_strict_and_infra_free():
    for case in discover_numbered_cases(ROOT):
        if int(case.name[:3]) not in LOCAL_IDS:
            continue
        spec = CaseSpec.load(case)
        assert spec.execution_class == "local_sandbox"
        assert spec.legacy_agent_fields == ()
        assert spec.candidate_image is None
```

- [ ] **Step 2: Implement deterministic migration script**

It adds explicit execution, canonical runtime requirements, structural
submission contract, and removes `[agent]` policy. It preserves scientific
instruction/public/reference/threshold/test bytes exactly.

- [ ] **Step 3: Run migration and byte-scope audit**

Run: `.venv/bin/python scripts/infra/migrate_case_contracts.py --write --scope local --paths-out /tmp/dftworld-local-migration-paths.txt`  
Run: `git diff --stat`  
Expected: only Local `task.toml` plus the migration script change.

- [ ] **Step 4: Fix build resolution and Case 006 image**

`build.sh <case>` checks root `Dockerfile` first, then legacy
`environment/Dockerfile`, resolves requirements through the runtime registry,
and fails before Agent startup if a digest is unavailable. Build
`dftworld-base-packmol` only at the authorized image gate.

- [ ] **Step 5: Make root pytest deterministic**

Add:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["jobs", "evidence", ".worktrees", ".pagent"]
```

- [ ] **Step 6: Run contract and simulated gold matrix**

Run: `.venv/bin/pytest -q tests/contracts/test_all_case_contracts.py tests/runtime/test_local_gold_matrix.py`  
Run: `.venv/bin/pytest -q tests`  
Expected: PASS; no duplicate-module collection errors.

- [ ] **Step 7: Authorized Docker gold gate**

After runtime qualification, run each Local hidden/gold submission through the
fresh Verifier. Expected: 37/37 structurally valid and the known gold outcome;
no Agent/API call occurs.

- [ ] **Step 8: Commit**

```bash
git add scripts/infra/migrate_case_contracts.py base-env-build/build.sh pyproject.toml \
  tests/contracts/test_all_case_contracts.py \
  tests/runtime/test_local_gold_matrix.py
git add --pathspec-from-file=/tmp/dftworld-local-migration-paths.txt
git commit -m "refactor(cases): migrate all local cases to infra v2 contract"
```

Before committing, inspect `git diff --cached --name-only`; the generated path
file must contain only 001–030 and 035–041 Local manifests.

---

### Task 16: Migrate 031–034 and 042 to the Generic HPC Contract

**Files:**
- Modify: `031-matclaw-cips-active-distillation/task.toml`
- Modify: `032-matclaw-cips-curie-temperature/task.toml`
- Modify: `033-matclaw-cips-domain-wall-search/task.toml`
- Modify: `034-ai2kit-water64-end-to-end-potential/task.toml`
- Modify: `042-go-water-dpmp/task.toml`
- Create per Case: `execution/runtime-requirements.yaml`
- Remove runtime authority from: the five Case Dockerfiles
- Create: `dftworld_bench/plugins/__init__.py`
- Create: `dftworld_bench/plugins/mlp.py`
- Test: `tests/hpc/test_all_hpc_cases_v2.py`
- Test: `tests/hpc/test_fake_adapter_matrix.py`

**Interfaces:**
- Shared MLP plugin consumes capabilities/runtime requirements only.
- No plugin function receives Case ID as a dispatch parameter.

- [ ] **Step 1: Write RED five-case matrix**

```python
HPC_CASES = ("031-", "032-", "033-", "034-", "042-")

def test_all_hpc_cases_resolve_same_executor_different_requirements():
    specs = [CaseSpec.load(find_case(prefix)) for prefix in HPC_CASES]
    assert {s.execution_class for s in specs} == {"hpc_controller"}
    assert all(isinstance(EXECUTOR_REGISTRY.resolve(s.execution_class), HpcExecutor)
               for s in specs)
    assert all(not s.legacy_agent_fields for s in specs)
```

- [ ] **Step 2: Move concrete runtime facts out of Case Dockerfiles**

Case files declare requirements such as CP2K, DeePMD/LAMMPS, ai2-kit,
deepmd-jax/DPMP and platform constraints. Runtime Registry resolves control,
remote compute, and Verifier digests. Case-specific science stays in public
inputs, instruction, submission contract, and Verifier parameters.

Run: `.venv/bin/python scripts/infra/migrate_case_contracts.py --write --scope hpc --paths-out /tmp/dftworld-hpc-migration-paths.txt`  
Expected: the path report contains only the five HPC Case contract/runtime
files owned by this task.

- [ ] **Step 3: Reconcile 042 schemas and stale resource hashes**

Make `CaseSpec`, target schema, and Case Factory agree on
`scientific_capabilities`. Replace the three unresolved resource-hash sentinel
values in 042 `profiles/resource.yaml` with the already evidenced hashes from
its compute-runtime lock, then regenerate evaluator manifest deterministically.

- [ ] **Step 4: Remove MatClaw hidden-solution controller semantics**

The Agent submits declared `job.yaml` commands through the common Gateway;
shared plugins never stage hidden `solution/`. Hidden solution remains
Verifier/reference-only. Retire `CASE_POLICIES` as Candidate execution authority.

- [ ] **Step 5: Run fake-adapter matrix**

Run: `.venv/bin/pytest -q tests/hpc/test_all_hpc_cases_v2.py tests/hpc/test_fake_adapter_matrix.py tests/hpc/test_process_conformance.py`  
Expected: all five cases submit, wait, fetch and settle through the same HpcExecutor.

- [ ] **Step 6: Real-site canary gate requiring explicit authorization**

Run one bounded no-science canary per resolved runtime plugin. Record real site
digest, job ownership, queue/run time, fetch hashes and token revocation. Do not
run expert science or ablation here.

- [ ] **Step 7: Commit**

```bash
git add --pathspec-from-file=/tmp/dftworld-hpc-migration-paths.txt
git add dftworld_bench/plugins tests/hpc/test_all_hpc_cases_v2.py \
  tests/hpc/test_fake_adapter_matrix.py
git commit -m "refactor(cases): migrate all hpc cases to generic execution"
```

Generate `/tmp/dftworld-hpc-migration-paths.txt` with the migration script and
review it before staging. It may list only the five task manifests, five
runtime-requirement files, five legacy Dockerfile authority removals, and 042's
resource/manifest refresh; it must exclude workspaces and unrelated evidence.

---

### Task 17: Release Preflight, Protocol v2, and Honest Historical Classification

**Files:**
- Modify: `dftworld_bench/experiments/release_builder.py`
- Create: `dftworld_bench/experiments/admission.py`
- Modify: `experiments/skill-ablation-v1/protocol.yaml`
- Modify: `releases/ablation-ready-v0.json` only by superseding it with a new file
- Create: `releases/infra-v2-pilot-v1.json`
- Modify: `experiments/skill-ablation-v1/invalid-runs.json`
- Test: `tests/experiments/test_release_reproducibility.py`
- Test: `tests/experiments/test_admission.py`
- Test: `tests/experiments/test_ablation_protocol.py`

**Interfaces:**
- Produces: `preflight_release(release, root) -> AdmissionReport`.
- Formal admission requires zero mismatches and a complete resolved lock.

- [ ] **Step 1: Write RED preflight tests**

```python
def test_edited_frozen_instruction_blocks_before_provider(release, tmp_repo, provider):
    mutate(tmp_repo / "031-matclaw-cips-active-distillation/instruction.md")
    report = preflight_release(release, tmp_repo)
    assert not report.admitted
    assert provider.calls == 0

def test_unrelated_untracked_file_does_not_block(release, tmp_repo):
    (tmp_repo / "notes.tmp").write_text("user data")
    assert preflight_release(release, tmp_repo).admitted
```

- [ ] **Step 2: Freeze every new identity component**

Release builder includes schemas, cases, tools/help, Harness/eval, profiles,
runtime digests, Gateway/Adapter, Verifiers, context/sampling, and experiment
manifest. Mutable tags are rejected.

- [ ] **Step 3: Version the experiment protocol**

Freeze treatment, 128/256/512 profile selection, token/cost/time/compute quotas,
API policy, concurrency, block randomization, replacement rules, and lock
comparison. Formal CLI override is schema-invalid.

- [ ] **Step 4: Classify existing attempts honestly**

Populate the append-only invalid ledger from existing Formal `INFRA_INVALID`
RunRecords. Mark runs made against mismatched instructions/release as
diagnostic-only; never rewrite their immutable RunRecords.

Explicitly include the 2026-08-20 031/032/033 With-Skill attempts: they used
moving worktree bytes while claiming the frozen `aae1bec` release, and the 033
replacement attempts additionally changed Candidate/Verifier image identity.
They remain diagnostic/invalid and are never resumed or reused as Formal
observations; Infra-v2 reruns use fresh run IDs.

- [ ] **Step 5: Keep 042 out of Formal release**

Include 042 only in the Infra-v2 Discovery/Pilot scope until G5–G12 derive true.

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/pytest -q tests/experiments tests/ablation/test_validate_pilot_pair.py`  
Expected: PASS and every release digest recomputes.

```bash
git add dftworld_bench/experiments experiments/skill-ablation-v1 \
  releases/infra-v2-pilot-v1.json tests/experiments \
  tests/ablation/test_validate_pilot_pair.py
git commit -m "feat(infra): freeze reproducible infra v2 pilot protocol"
```

---

### Task 18: Adversarial Acceptance Dashboard and Activation

**Files:**
- Create: `scripts/infra/activate_v2.py`
- Create: `tests/adversarial/test_infra_v2_attacks.py`
- Create: `tests/e2e/test_infra_v2_simulated.py`
- Modify: `docs/case-factory/ACCEPTANCE-DASHBOARD.md`
- Create: `docs/architecture/INFRA-V2-OPERATIONS.md`

**Interfaces:**
- Produces: `activate_v2.py --json` with seven hard gates and per-test evidence.
- Exit 0 only when all gates are true; no hand-written pass booleans.

- [ ] **Step 1: Encode the adversarial traceability matrix**

The test file contains one named test for every approved attack:

```python
ATTACK_TESTS = {
    "gateway-direct-http": "test_gateway_rejects_direct_path_escape",
    "collector-symlink": "test_collector_never_dereferences_symlink",
    "release-claim-drift": "test_release_preflight_rejects_frozen_byte_drift",
    "pair-confound": "test_lock_diff_rejects_undeclared_difference",
    "nested-api-retry": "test_exactly_one_retry_owner",
    "duplicate-slurm": "test_resume_reuses_operation_job",
    "wait-consumes-turn": "test_external_wait_does_not_charge_turn",
    "resume-profile-drift": "test_resume_rejects_changed_lock",
    "secret-in-candidate": "test_candidate_has_no_provider_secret",
    "formal-endpoint-fallback": "test_formal_has_no_endpoint_fallback",
    "resource-imbalance": "test_pair_freezes_resource_enforcement",
    "double-terminal": "test_lifecycle_rejects_second_terminal",
    "infra-as-zero": "test_infra_invalid_never_counts_scientifically",
    "verifier-app-venv": "test_verifier_uses_opt_runtime",
    "unequal-token-budget": "test_pair_compares_token_budget",
    "premature-042-promotion": "test_042_requires_g5_through_g12",
    "identity-dispatch": "test_shared_sources_have_no_case_identity_branch",
    "033-api-science-separation": "test_033_504_is_infra_while_slope_remains_diagnostic",
    "unbounded-local-tool": "test_unbounded_tool_is_killed_and_agent_resumes",
    "gpu-subtype-confusion": "test_full_gpu_profile_rejects_mig_allocation",
}
```

The 033 regression constructs an API-504 result alongside a workspace whose
recorded best slope is below 0.3. It asserts the attempt is
`INFRA_INVALID/API_TRANSIENT_EXHAUSTED`, the slope remains diagnostic evidence,
and no scientific PASS/FAIL is counted for that interrupted attempt.

- [ ] **Step 2: Implement derived gate evaluation**

```python
GATES = {
    "INFRA-G-AGENT-CONFIG": AGENT_CONFIG_TESTS,
    "INFRA-G-COMPARISON": COMPARISON_TESTS,
    "INFRA-G-DURABILITY": DURABILITY_TESTS,
    "INFRA-G-API": API_TESTS,
    "INFRA-G-EXECUTION": EXECUTION_TESTS,
    "INFRA-G-TRUST": TRUST_TESTS,
    "INFRA-G-REPRODUCIBILITY": REPRO_TESTS,
}
```

The dashboard derives status from pytest/JUnit evidence, release preflight,
runtime qualification receipts, and fake-adapter conformance. Missing evidence
is FAIL.

- [ ] **Step 3: Run the full simulated acceptance suite**

Run: `.venv/bin/pytest -q tests`  
Run: `.venv/bin/python scripts/infra/activate_v2.py --mode simulated --json`  
Expected: all deterministic and fake-adapter gates PASS; real-site/pilot gates
remain explicitly `NOT_RUN`, so Formal stays disabled.

- [ ] **Step 4: Authorized real activation sequence**

After explicit authorization:

1. qualify all OCI/SIF runtimes;
2. run one real-site canary per HPC runtime plugin;
3. run one `pilot-infra` No-Skill/With-Skill pair for each of 031–034;
4. run 042 `discovery-long` as Discovery only;
5. verify pair locks differ only by Skill treatment;
6. confirm turn exhaustion is below 10% of healthy Pilots;
7. restore evidence in a fresh process and rerun Verifier;
8. run `activate_v2.py --mode real --json`.

No Formal arm starts automatically. The activation report only changes the
admission gate; a separate user-authorized command starts Formal experiments.

- [ ] **Step 5: Commit**

```bash
git add scripts/infra/activate_v2.py tests/adversarial tests/e2e \
  docs/case-factory/ACCEPTANCE-DASHBOARD.md docs/architecture/INFRA-V2-OPERATIONS.md
git commit -m "test(infra): add adversarial infra v2 activation gate"
```

---

## Adversarial Traceability Summary

| Risk | Owning task | Primary test |
|---|---:|---|
| Candidate bypasses CLI and escapes host workspace | 10 | `test_gateway_rejects_direct_path_escape` |
| Collector dereferences valid/broken symlink | 13 | `test_collector_rejects_file_symlink` |
| Run claims old release over edited bytes | 17 | `test_edited_frozen_instruction_blocks_before_provider` |
| NS/WS differs in undeclared field | 4 | `test_skill_ablation_rejects_confounds` |
| SDK and Harness both retry | 8 | `test_exactly_one_retry_owner` |
| Resume duplicates Slurm job | 10 | `test_resume_reuses_operation_job` |
| API/HPC wait consumes model turn | 7, 9 | `test_api_retry_does_not_consume_turn` |
| Resume silently changes profile | 6, 9 | `test_checkpoint_rejects_changed_lock` |
| API secret reaches Candidate | 2, 8 | `test_secret_never_serializes` + sandbox test |
| Formal silently falls back endpoint/model | 3, 8 | `test_formal_override_is_rejected` |
| Resource/time imbalance confounds comparison | 3, 4, 17 | lock comparator matrix |
| Lifecycle contains two terminals | 14 | `test_record_has_exactly_one_terminal` |
| Infra failure becomes reward zero | 5, 14 | Result taxonomy tests |
| `/app` mount hides or supplies Python runtime | 12, 13 | runtime qualification + verifier test |
| Equal turns hide unequal token/context budgets | 3, 4, 7 | lock comparator + budget tests |
| 042 promoted without real reference evidence | 16, 18 | `test_042_requires_g5_through_g12` |
| New Case requires identity branch | 11, 16 | source scan + dummy-case extension |

## Completion Definition

Implementation is complete only when:

- all 18 task commits exist and their scoped tests pass;
- `.venv/bin/pytest -q tests` passes from repository root;
- `activate_v2.py --mode simulated` passes deterministic gates;
- all 001–042 strict CaseSpec tests pass;
- 37 Local gold Verifier runs pass after authorized Docker qualification;
- five HPC fake-adapter cases pass the same executor contract;
- authorized real-site canaries and Pilot pairs produce complete RunRecord v2,
  resolved lock, event chain, job audit, seal and Verifier evidence;
- 042 remains `benchmark_valid=false` until its independent G5–G12 process;
- Formal admission stays disabled until the real activation report is complete.
