# HpcDispatcher Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce DFTWorld's real-HPC execution to one Agent-facing `bench-hpc`
interface and one trusted `HpcDispatcher` entry while preserving real the site
Slurm execution, isolation, durable recovery, evidence, and existing Case/
Verifier contracts.

**Architecture:** `HpcDispatcher` is initially a zero-behavior-change façade
over the existing Gateway/GatewayRuntime/JobSpec/SlurmAdapter stack. Public
terminology becomes `bench-hpc`, `ExecutionRequest`, `HpcDispatcher`, and
`SiteProfile`; internal components are removed only after façade parity,
ProcessDriver conformance, authorized real-site canaries, and five-lineage
Pilots pass.

**Tech Stack:** Python 3.12+, JSON Schema 2020-12, pytest, Docker Candidate,
HTTP/JSON bench-hpc client, SSH/rsync, real Slurm, Apptainer/SIF, SHA-256.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-08-22-hpc-dispatcher-simplification-design.md`.
- DPDispatcher package is not added to main dependencies.
- Formal has one HPC driver: real Slurm. ProcessDriver is CI/fault injection only.
- Candidate never receives SSH credentials, SiteProfile contents, personal HOME,
  scheduler config, remote root, hidden reference, solution, or Verifier assets.
- Case-specific branches in Dispatcher/Driver are forbidden.
- Agent-facing identity is `(run_id, operation_id, attempt)`; scheduler job ID
  remains internal provenance.
- Scientific retries are Agent decisions with incremented attempts; only bounded
  transient transport retries are automatic.
- The first v2 HPC Pilot uses `agent_turn_limit = 128` for both NS and WS;
  external queue wait/polling consumes no model turns.
- Model/provider/API credentials and all budgets are Harness-owned; Case,
  Candidate request and Skill cannot set or override them.
- Remote outputs enter submission only after explicit Agent fetch.
- Existing historical readers and evidence remain readable throughout migration.
- No Docker image build, SSH, Slurm submission, or real canary without the
  task's explicit authorization gate.
- Every task ends with scoped GREEN tests and a task-local commit.

## First-Principles Review Gate

Every task must preserve these invariants:

```text
declared treatment only
untrusted decision / trusted execution
logical operation-attempt identity
fail closed on unknown state
scheduler success != scientific success
explicit fetch only
trusted evidence origin
no hidden host reachability
versioned protocol evolution
proof before deletion
```

A task is not complete merely because a class or test exists. Its RED/GREEN
test must traverse the production callsite named in that task.

---

## Pre-Implementation Gate: Stabilize Infra v2

Implementation starts in a fresh worktree based on a clean
`infra-v2-upgrade` baseline. Before Task 1:

1. Resolve and commit or discard only with authorization all R3 working-tree
   changes; remove generated `dftworld.egg-info` and `.plan.md` from the
   implementation baseline.
2. Restore Task 16 fake-adapter matrix after typed request hardening.
3. Correctly close I3/I5/I6/I7 using production-path tests, not documentation
   assertions.
4. Run `.venv/bin/pytest -q -p no:cacheprovider tests` outside the socket-
   restricted sandbox.
5. Record exact expected Task-17-owned release failures; no other failure is
   permitted.
6. Create an isolated branch/worktree named `hpc-dispatcher-simplification`.

If this gate is not met, stop. Do not stack a façade refactor on an ambiguous
or dirty baseline.

---

### Task 1: Add a Zero-Behavior-Change HpcDispatcher Façade

**Files:**
- Create: `dftworld_bench/hpc/dispatcher.py`
- Modify: `dftworld_bench/hpc/__init__.py`
- Test: `tests/hpc/test_dispatcher_facade.py`
- Test: `tests/hpc/test_dispatcher_parity.py`

**Interfaces:**
- Produces: `HpcDispatcher.open_run(run_id, *, workspace) -> DispatcherSession`
- Produces: `DispatcherSession` methods for seven operations plus `settle/close`.
- Consumes existing `GatewayRuntime`, `GatewayLease`, `Gateway`, and adapters.

- [ ] **Step 1: Write façade RED tests**

```python
def test_dispatcher_exposes_one_trusted_entry(tmp_path):
    dispatcher = HpcDispatcher.process_test(tmp_path / "site")
    session = dispatcher.open_run("run-1", workspace=tmp_path / "workspace")
    assert session.run_id == "run-1"
    assert set(session.capabilities()) >= {"adapter", "states"}

def test_dispatcher_close_revokes_lease(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-1", workspace=tmp_path / "workspace"
    )
    session.close()
    with pytest.raises(DispatcherClosedError):
        session.usage()
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/hpc/test_dispatcher_facade.py`
Expected: import failure for `dftworld_bench.hpc.dispatcher`.

- [ ] **Step 3: Implement thin composition only**

```python
class HpcDispatcher:
    def __init__(self, runtime: GatewayRuntime, adapter_config: dict) -> None:
        self._runtime = runtime
        self._adapter_config = dict(adapter_config)

    def open_run(self, run_id: str, *, workspace: Path) -> DispatcherSession:
        config = {**self._adapter_config, "workspace_root": str(workspace)}
        lease = self._runtime.start(run_id, config)
        return DispatcherSession(lease)
```

`DispatcherSession` calls existing Gateway operations with its private token;
it adds no new retry, path, quota, or state logic.

- [ ] **Step 4: Prove seven-operation parity**

For identical validated requests, compare direct Gateway and façade outputs for
capabilities, submit, status, logs, fetch, cancel, and usage. Normalize only
non-deterministic token/timestamp fields.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest -q tests/hpc/test_dispatcher_facade.py tests/hpc/test_dispatcher_parity.py tests/hpc/test_gateway.py tests/hpc/test_gateway_runtime.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dftworld_bench/hpc/dispatcher.py dftworld_bench/hpc/__init__.py \
  tests/hpc/test_dispatcher_facade.py tests/hpc/test_dispatcher_parity.py
git commit -m "feat(hpc): add zero-change HpcDispatcher facade"
```

---

### Task 2: Version ExecutionRequest and Operation-Attempt Identity

**Files:**
- Create: `schemas/execution-request.schema.json`
- Modify: `dftworld_bench/hpc/job.py`
- Create: `dftworld_bench/hpc/request.py`
- Create: `dftworld_bench/hpc/staging.py`
- Modify: `schemas/hpc-job.schema.json`
- Test: `tests/hpc/test_execution_request.py`
- Create: `tests/hpc/test_input_staging.py`
- Test: `tests/hpc/test_job_contract.py`

**Interfaces:**
- Produces: `ExecutionRequestV2`
- Produces: `seal_inputs(request, workspace, staging_root) -> InputManifest`
- Compatibility: `JobSpec` remains a v1 reader/alias.
- Key: `(run_id, operation_id, attempt)`.

- [ ] **Step 1: Write RED identity and validation tests**

```python
def test_request_requires_operation_and_positive_attempt(valid_payload):
    valid_payload.pop("operation_id")
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(valid_payload)

def test_request_rejects_raw_scheduler_flags(valid_payload):
    valid_payload["scheduler_flags"] = ["#SBATCH --partition=secret"]
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(valid_payload)

def test_transport_identity_is_run_operation_attempt(request):
    assert request.idempotency_key("run-a") == "run-a:cp2k-round-01:1"

def test_attempt_must_be_monotonic(session, request):
    with pytest.raises(RequestError, match="attempt 1"):
        session.submit(request.with_attempt(2))
    session.submit(request.with_attempt(1))
    with pytest.raises(RequestError, match="terminal"):
        session.submit(request.with_attempt(2))

def test_input_swap_after_validation_is_rejected(tmp_path, request):
    source = tmp_path / "workspace" / "input.inp"
    source.parent.mkdir()
    original = b"&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n"
    source.write_bytes(original)
    request = request.with_input(
        "input.inp",
        sha256=hashlib.sha256(original).hexdigest(),
        size_bytes=len(original),
    )
    source.write_bytes(b"changed after validation")
    with pytest.raises(StagingError, match="digest"):
        seal_inputs(request, source.parent, tmp_path / "sealed")
```

- [ ] **Step 2: Implement v2 immutable request**

Fields are operation ID, positive attempt, digest-pinned runtime, argv command,
content-addressed input entries (`path`, `sha256`, `size_bytes`), relative
outputs, typed resources, and allowlisted environment. Reject unknown fields,
absolute/backslash/parent traversal, symlinks, command strings, raw scheduler
flags, and mutable runtime tags. Stage from an immutable upload snapshot:
reopen every input without following links, then recheck type, size and digest
before accepting the request. A Candidate edit between initial validation and
staging must fail rather than change submitted bytes.

Enforce monotonic attempts without gaps: attempt 1 is first; attempt `n+1`
requires attempt `n` to be scheduler-terminal; concurrent attempts for one
operation are rejected in protocol v2.

- [ ] **Step 3: Preserve v1 compatibility**

`JobSpec.load()` remains for old records/CLI files. New submit paths upgrade a
v1 request by requiring operation ID/attempt from the submit envelope; Formal
writers emit v2 only.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_execution_request.py tests/hpc/test_input_staging.py tests/hpc/test_job_contract.py`
Expected: PASS.

```bash
git add schemas/execution-request.schema.json schemas/hpc-job.schema.json \
  dftworld_bench/hpc/request.py dftworld_bench/hpc/job.py \
  dftworld_bench/hpc/staging.py tests/hpc/test_execution_request.py \
  tests/hpc/test_input_staging.py tests/hpc/test_job_contract.py
git commit -m "feat(hpc): version execution requests by operation attempt"
```

---

### Task 3: Make Operation/Attempt the Agent-Facing CLI Identity

**Files:**
- Modify: `dftworld_bench/hpc/__main__.py`
- Modify: `dftworld_bench/hpc/client.py`
- Modify: `dftworld_bench/hpc/http_server.py`
- Modify: `dftworld_bench/hpc/gateway.py`
- Modify: `dftworld_bench/hpc/audit.py`
- Test: `tests/hpc/test_client.py`
- Create: `tests/hpc/test_operation_attempts.py`

**Interfaces:**
- `submit` returns operation, attempt, internal job ID and duplicate state.
- `status/logs/fetch/cancel` accept operation ID and optional attempt.
- Legacy job-ID reads remain supported only in compatibility routes.
- Audit produces fsynced `SUBMIT_INTENT` and `SUBMIT_ACCEPTED` events.

- [ ] **Step 1: Write retry lineage RED tests**

```python
def test_two_scientific_attempts_have_distinct_jobs(session, request):
    first = session.submit(request.with_attempt(1))
    session.driver.finish(first.job_id, state="FAILED")
    second = session.submit(request.with_attempt(2))
    assert first.operation_id == second.operation_id
    assert first.job_id != second.job_id

def test_duplicate_transport_submit_reuses_same_attempt(session, request):
    one = session.submit(request.with_attempt(1))
    duplicate = session.submit(request.with_attempt(1))
    assert duplicate.job_id == one.job_id
    assert duplicate.duplicate is True

def test_crash_after_sbatch_adopts_exact_marker_without_resubmit(session, request):
    session.driver.fail_after_scheduler_accept_once()
    with pytest.raises(TransportUnknown):
        session.submit(request.with_attempt(1))
    recovered = session.submit(request.with_attempt(1))
    assert recovered.duplicate is True
    assert session.driver.physical_submit_count == 1
```

- [ ] **Step 2: Change ownership maps**

Store `run_id -> operation_id -> attempt -> scheduler job`. Require operation
and attempt on submit. `status(operation)` resolves the latest attempt; structured
output includes all attempt states.

Before scheduler submission, append and fsync a `SUBMIT_INTENT` carrying an
opaque exact-match scheduler marker. Record `SUBMIT_ACCEPTED` only after the
scheduler ID is known. Recovery of an unresolved intent queries that marker,
adopts exactly one matching scheduler job, fails closed on zero/multiple
ambiguous matches, and never issues a blind second `sbatch`.

- [ ] **Step 3: Version CLI without breaking help**

```text
bench-hpc status OPERATION_ID [--attempt N]
bench-hpc logs OPERATION_ID [--attempt N]
bench-hpc fetch OPERATION_ID OUTPUT_DIR [--attempt N]
bench-hpc cancel OPERATION_ID [--attempt N]
```

Keep a hidden compatibility parser for historical job IDs, but do not document
it as the v2 Agent interface.

Protocol v1 CLI behavior and help text are frozen for historical releases.
Protocol v2 is selected by resolved tool identity; never reinterpret a v1
command under v2 semantics in place.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_client.py tests/hpc/test_operation_attempts.py tests/hpc/test_gateway_security.py`
Expected: PASS.

```bash
git add dftworld_bench/hpc/__main__.py dftworld_bench/hpc/client.py \
  dftworld_bench/hpc/http_server.py dftworld_bench/hpc/gateway.py \
  dftworld_bench/hpc/audit.py tests/hpc/test_client.py \
  tests/hpc/test_operation_attempts.py
git commit -m "feat(hpc): expose operation-attempt lifecycle"
```

---

### Task 4: Introduce Driver Boundary Without Moving Implementations

**Files:**
- Create: `dftworld_bench/hpc/drivers/__init__.py`
- Create: `dftworld_bench/hpc/drivers/base.py`
- Create: `dftworld_bench/hpc/drivers/slurm.py`
- Create: `dftworld_bench/hpc/drivers/process.py`
- Modify: `dftworld_bench/hpc/gateway_runtime.py`
- Create: `tests/hpc/test_driver_conformance.py`

**Interfaces:**
- `HpcDriver` protocol: capabilities/submit/find/status/logs/fetch/cancel/usage/settle.
- `SlurmDriver` delegates to existing `SlurmAdapter`.
- `ProcessDriver` delegates to existing `ProcessTestAdapter`.

- [ ] **Step 1: Write shared conformance RED tests**

Run the same request lineage, ownership, cancellation, fetch and settlement
tests against both drivers. ProcessDriver executes; SlurmDriver uses scripted
transport only.

- [ ] **Step 2: Implement delegation wrappers**

No file moves and no scheduler behavior changes. Wrappers translate only
method names/types and carry operation-attempt identity.

- [ ] **Step 3: Restrict Formal driver selection**

`RunMode.FORMAL` rejects ProcessDriver. Smoke/Pilot tests may select it through
an explicit non-formal profile.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_driver_conformance.py tests/hpc/test_process_conformance.py tests/hpc/test_slurm_adapter.py`
Expected: PASS.

```bash
git add dftworld_bench/hpc/drivers dftworld_bench/hpc/gateway_runtime.py \
  tests/hpc/test_driver_conformance.py
git commit -m "refactor(hpc): hide adapters behind driver contract"
```

---

### Task 5: Freeze Trusted SiteProfile and Credential Boundary

**Files:**
- Create: `schemas/hpc-site-profile.schema.json`
- Create: `dftworld_bench/hpc/site_profile.py`
- Create: `infra/config/hpc-site-profiles.example.toml`
- Modify: `infra/config/site-profiles.toml`
- Test: `tests/hpc/test_site_profile.py`
- Test: `tests/config/test_profiles.py`

**Interfaces:**
- Produces: immutable `HpcSiteProfile` and secret-free digest.
- Credential provider is injected separately and never serializable.

- [ ] **Step 1: Write RED schema/secret tests**

```python
def test_public_profile_rejects_credential_value(profile):
    profile["ssh_private_key"] = "PRIVATE"
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(profile)

def test_profile_digest_excludes_secret_but_binds_policy(profile):
    site = HpcSiteProfile.from_dict(profile)
    assert site.digest.startswith("sha256:")
    assert "hostname" not in site.public_identity()

def test_candidate_capabilities_are_abstract(profile):
    capabilities = HpcSiteProfile.from_dict(profile).public_capabilities()
    encoded = json.dumps(capabilities)
    assert "acct-blocked" not in encoded
    assert "partition" not in encoded
    assert set(capabilities["resource_classes"]) == {"cpu", "gpu"}
```

The digest must bind the sanitized connection target/user identity without
including credential bytes. Changing target, account, partition, QOS, remote
root policy or runtime store changes the digest; rotating an equivalent
ephemeral key does not.

- [ ] **Step 2: Implement exact public/private split**

Public/profile schema carries site ID, scheduler type, resource ceilings,
runtime policy, GPU mapping, transfer/cleanup policy and credential profile ID.
Private connection values live outside Git and are resolved only by trusted
Harness.

Candidate-visible `capabilities` exposes abstract resource classes, ceilings
and supported operations only. It must not serialize scheduler account,
partition/QOS names, SSH target/user, remote root or credential profile ID.

- [ ] **Step 3: Encode the site policy without credentials**

Record account `acct-blocked`, cpu/gpu queues, normal/long QOS, `--gres=gpu:1`,
Apptainer requirement, runtime store policy and limits. Host/user/SSH key and
exact remote root remain private.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_site_profile.py tests/config/test_profiles.py`
Expected: PASS.

```bash
git add schemas/hpc-site-profile.schema.json dftworld_bench/hpc/site_profile.py \
  infra/config/hpc-site-profiles.example.toml infra/config/site-profiles.toml \
  tests/hpc/test_site_profile.py tests/config/test_profiles.py
git commit -m "feat(hpc): freeze trusted site policy identity"
```

---

### Task 6: Generate and Qualify the Trusted Apptainer Wrapper

**Files:**
- Create: `dftworld_bench/hpc/runtime_wrapper.py`
- Modify: `dftworld_bench/hpc/adapters/slurm.py`
- Create: `tests/hpc/test_runtime_wrapper.py`
- Create: `tests/hpc/test_runtime_containment.py`

**Interfaces:**
- Produces: `render_runtime_wrapper(request, site, run_dir) -> argv/script`.
- Candidate controls only validated argv and run-relative inputs.

- [ ] **Step 1: Write adversarial RED tests**

Reject Agent attempts to add bind flags, disable containment, choose host paths,
replace runtime, inject SBATCH directives, access HOME, or export blocked env.
Also reject nested Apptainer/Singularity invocation and any executable path that
could reintroduce host bind control inside the SIF.

- [ ] **Step 2: Implement deterministic trusted wrapper**

Use frozen Apptainer path/runtime digest and exactly one rw run bind. Consume
only the Dispatcher-sealed input snapshot, not live Candidate paths. Render
command with `shlex.join`; scheduler headers come only from SiteProfile.

- [ ] **Step 3: Add behavioral qualification fixture**

Inside a test SIF/runtime, assert `/workspace` works while HOME, old solution,
other-run, credential and arbitrary host paths are unreadable. Missing proof
fails qualification.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_runtime_wrapper.py tests/hpc/test_runtime_containment.py tests/hpc/test_slurm_adapter.py`
Expected: PASS.

```bash
git add dftworld_bench/hpc/runtime_wrapper.py dftworld_bench/hpc/adapters/slurm.py \
  tests/hpc/test_runtime_wrapper.py tests/hpc/test_runtime_containment.py
git commit -m "feat(hpc): enforce trusted contained scientific runtime"
```

---

### Task 7: Route Production Through HpcDispatcher Only

**Files:**
- Modify: `dftworld_bench/executors/hpc.py`
- Modify: `dftworld_bench/core/coordinator.py`
- Modify: `eval.py`
- Test: `tests/hpc/test_dispatcher_production_path.py`
- Modify: `tests/executors/test_no_identity_dispatch.py`

**Interfaces:**
- HpcExecutor receives one dispatcher dependency/session.
- Coordinator never imports Gateway, adapters, transport or SiteProfile details.

- [ ] **Step 1: Write source and behavior RED tests**

Assert production modules contain no direct Gateway/Adapter construction and
all five HPC Cases resolve the same HpcDispatcher path.

- [ ] **Step 2: Inject dispatcher at composition root**

Trusted `eval` composition resolves SiteProfile/credential, constructs
HpcDispatcher, and passes a run session to HpcExecutor. Candidate receives only
bench-hpc URL/token.

- [ ] **Step 3: Preserve durable wait semantics**

Coordinator records request validation, operation attempt, submit, external
wait, terminal state, fetch and settlement. Queue wait does not consume model
turns. Resume consults dispatcher ledger before submit.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_dispatcher_production_path.py tests/hpc/test_fake_adapter_matrix.py tests/executors/test_no_identity_dispatch.py tests/core/test_coordinator.py`
Expected: PASS.

```bash
git add dftworld_bench/executors/hpc.py dftworld_bench/core/coordinator.py eval.py \
  tests/hpc/test_dispatcher_production_path.py \
  tests/executors/test_no_identity_dispatch.py
git commit -m "refactor(hpc): make dispatcher sole production entry"
```

---

### Task 8: Update the Bundled hpc-submit Skill

**Files:**
- Modify: `base-env-build/skills/hpc-submit/SKILL.md`
- Modify: `base-env-build/skills/hpc-submit/references/running.md`
- Modify: `base-env-build/skills/hpc-submit/references/validation.md`
- Modify: `base-env-build/skills/hpc-submit/references/errors.md`
- Modify: `base-env-build/skills/deepmd/SKILL.md`
- Modify: `base-env-build/skills/deepmd/references/running.md`
- Modify: `base-env-build/skills/lammps/SKILL.md`
- Modify: `base-env-build/skills/lammps/references/running.md`
- Create: `base-env-build/skills/hpc-submit/examples/execution-request.yaml`
- Create: `tests/skills/test_hpc_submit_benchmark_contract.py`

**Interfaces:**
- Skill teaches bench-hpc descriptor lifecycle, not raw SSH/site trivia.
- NS sees the same CLI/help but no Skill content.

- [ ] **Step 1: Write RED leak/treatment tests**

Assert the Skill contains no hostname, account, partition answer, remote root,
Case ID, threshold, solution path or SSH key instruction. Assert it teaches all
seven operations, operation attempts, parser gating and explicit fetch.

- [ ] **Step 2: Rewrite benchmark procedure**

Procedure: capabilities -> local scientific preflight -> request -> submit ->
status/logs -> diagnose -> new attempt -> fetch -> engine parser -> next stage.
Retain `COMPLETED != scientific success`, checkpointing, OOM/timeout recovery,
and no blind retry.

- [ ] **Step 3: Remove raw remote workflow instructions**

Remove SSH bootstrap, rsess/tmux, direct module discovery, raw sbatch/squeue/
sacct and scp/rsync from the Candidate-facing benchmark Skill. Site-specific
facts are capabilities/SiteProfile responsibilities.

Dependent bundled skills must route to the descriptor workflow and must not
reintroduce `~/.cluster-agents.md`, raw SSH, partition or module instructions.
Run the recursive assertion across all bundled Skills, including unchanged
`comp-chem-workflow`, `cp2k`, and `vasp` routing text; a new raw-remote
instruction anywhere in the Candidate-visible bundle fails the test.
The user's global personal `hpc-submit` Skill is not modified by this benchmark
bundle task.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/skills/test_hpc_submit_benchmark_contract.py tests/experiments/test_lock_comparison.py`
Expected: PASS; Skill digest differs only in WS treatment.

```bash
git add base-env-build/skills/hpc-submit base-env-build/skills/deepmd/SKILL.md \
  base-env-build/skills/deepmd/references/running.md \
  base-env-build/skills/lammps/SKILL.md \
  base-env-build/skills/lammps/references/running.md \
  tests/skills/test_hpc_submit_benchmark_contract.py
git commit -m "docs(skill): align hpc-submit with bench-hpc dispatcher"
```

---

### Task 9: Complete Fetch, Evidence and Settlement Semantics

**Files:**
- Modify: `dftworld_bench/hpc/dispatcher.py`
- Modify: `dftworld_bench/hpc/audit.py`
- Modify: `dftworld_bench/contracts/run_record.py`
- Modify: `schemas/run-record.schema.json`
- Create: `tests/hpc/test_dispatcher_settlement.py`
- Modify: `tests/contracts/test_run_record_v2.py`

**Interfaces:**
- Produces: `AttemptRecord`, `ArtifactManifest`, `SettlementReport`.
- RunRecord stores operation-attempt-job lineage and explicit fetch evidence.

- [ ] **Step 1: Write RED evidence tests**

Test missing backward files, hash mismatch, Agent-not-fetched output, orphan
queued/running jobs, transport-unknown state, cancel failure, destination
symlink/escape, pre-existing destination collision, partial transfer and
repeated settlement.

Add remote malicious-node fixtures: absolute and escaping symlinks, hardlinks,
FIFO/socket/device, and a symlink targeting an old
`/public/home/<site-user>/dftworld2-runs/...` path. None may be fetched or hashed by
following its target. A declared archive fixture must transfer as opaque bytes
without extraction.

- [ ] **Step 2: Implement explicit fetch**

Fetch accepts only declared output paths and trusted remote `lstat`/realpath
results. It rejects destination links/escapes, copies into a fresh
same-filesystem staging directory, verifies hashes, fsyncs data and manifest,
then atomically publishes with no overwrite. Archives remain opaque regular
files. Remote diagnostic evidence is sealed separately and cannot repair
submission.

- [ ] **Step 3: Implement idempotent settlement**

Disable new submissions, query every owned attempt, wait/cancel by frozen
policy, collect usage/evidence, revoke capability and produce one immutable
report. Repeating settlement returns the same digest.

Settlement may address only exact scheduler IDs in the run ledger; tests seed
unrelated same-user jobs with matching name prefixes and prove they are never
queried for cancellation.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest -q tests/hpc/test_dispatcher_settlement.py tests/contracts/test_run_record_v2.py tests/core/test_harness.py`
Expected: PASS.

```bash
git add dftworld_bench/hpc/dispatcher.py dftworld_bench/hpc/audit.py \
  dftworld_bench/contracts/run_record.py schemas/run-record.schema.json \
  tests/hpc/test_dispatcher_settlement.py tests/contracts/test_run_record_v2.py
git commit -m "feat(hpc): seal operation attempts and settlement evidence"
```

---

### Task 10: Simulated Driver and Fault-Injection Acceptance

**Files:**
- Create: `tests/hpc/test_dispatcher_faults.py`
- Modify: `tests/hpc/test_driver_conformance.py`
- Create: `scripts/infra/activate_v2.py`
- Modify: `docs/case-factory/ACCEPTANCE-DASHBOARD.md`

**Interfaces:**
- Adds D0–D12 dispatcher gates to derived activation status.

- [ ] **Step 1: Add deterministic faults**

Cover SSH connect/query transient failure, transfer partial failure, Candidate
input mutation after validation, duplicate submit, crashes immediately before
and after `sbatch`, zero/multiple exact-marker recovery matches,
PENDING/RUNNING/FAILED/TIMEOUT/CANCELLED, missing output, stale remote run,
settlement cancellation and token revocation.

- [ ] **Step 2: Prove Formal rejects ProcessDriver**

Smoke/Pilot may use it; Formal construction and release admission fail before
Candidate startup if the resolved driver is not qualified Slurm.

- [ ] **Step 3: Run simulated activation**

Run: `.venv/bin/pytest -q tests/hpc tests/core/test_coordinator.py tests/contracts/test_run_record_v2.py`
Run: `.venv/bin/python scripts/infra/activate_v2.py --mode simulated --json`
Expected: D0–D5 and deterministic parts of D8–D12 PASS; real-site gates remain
`NOT_RUN` and Formal stays disabled.

- [ ] **Step 4: Commit**

```bash
git add tests/hpc/test_dispatcher_faults.py tests/hpc/test_driver_conformance.py \
  scripts/infra/activate_v2.py docs/case-factory/ACCEPTANCE-DASHBOARD.md
git commit -m "test(hpc): add dispatcher fault and activation gates"
```

---

### Task 11: Authorized the site Canary Qualification

**Files:**
- Create: `scripts/infra/qualify_hpc_dispatcher.py`
- Create: `docs/architecture/HPC-DISPATCHER-OPERATIONS.md`
- Create only after run: content-addressed qualification receipt under the
  exact path `evidence/hpc-dispatcher/qualification/site-v1/receipt.json`,
  with its digest anchored by the evaluator manifest/release builder.

**Interfaces:**
- Read-only/pre-submit profile checks plus three explicitly authorized jobs.
- No Agent/model call and no five-Case science in this task.

- [ ] **Step 1: Preflight without submission**

Read private site guide; validate profile digest, SSH route, remote root
containment, quota, runtime/SIF digest, Slurm mapping and no hidden bind.

- [ ] **Step 2: Stop for explicit authorization**

Request approval for exactly:

1. one CPU echo/hostname job;
2. one short CP2K ENERGY job;
3. one short GPU allocation/runtime probe using `--gres=gpu:1`.

- [ ] **Step 3: Execute and record canaries**

Record operation/attempt/job IDs, scripts, status history, sacct, ReqTRES/
AllocTRES, trusted device probe, logs, fetch hashes, usage and settlement.

- [ ] **Step 4: Fresh containment probe**

Inside the pinned SIF prove run workspace is readable/writable and personal
HOME, old solution/reference, other runs and credentials are unreadable.

- [ ] **Step 5: Verify receipt and commit code/docs only**

Run: `.venv/bin/python scripts/infra/qualify_hpc_dispatcher.py --verify evidence/hpc-dispatcher/qualification/site-v1/receipt.json`
Expected: all real gates PASS and receipt digest recomputes.

```bash
git add scripts/infra/qualify_hpc_dispatcher.py \
  docs/architecture/HPC-DISPATCHER-OPERATIONS.md
git commit -m "feat(hpc): add real dispatcher qualification workflow"
```

Evidence is committed only through the repository's authorized release/evidence
workflow; never stage private SiteProfile or credentials.

---

### Task 12: Five-Lineage Pilot, Release and Legacy Cleanup

**Files:**
- Create: `experiments/skill-ablation-v2/protocol.yaml`
- Modify: `dftworld_bench/experiments/release_builder.py`
- Create: `releases/hpc-dispatcher-v1.json`
- Modify: `scripts/infra/activate_v2.py`
- Modify: `base-env-build/matclaw-cips-controller/Dockerfile`
- Modify: `base-env-build/ai2kit-controller/Dockerfile`
- Modify: `tests/hpc/test_eval_run_identity.py`
- Delete after all gates: `scripts/matclaw_hpc_controller.py`
- Delete after all gates: `scripts/matclaw_hpc_gateway.py`
- Delete after all gates: `tests/test_matclaw_hpc_controller.py`
- Delete after all gates: `tests/test_matclaw_hpc_gateway.py`
- Test: `tests/experiments/test_release_reproducibility.py`
- Test: `tests/hpc/test_all_hpc_cases_v2.py`

**Interfaces:**
- Formal release binds Dispatcher, Driver, SiteProfile, request schema, runtime,
  tool/help, Agent/API/Skill/Verifier/budget identities.

- [ ] **Step 1: Freeze Pilot protocol before observations**

Define one NS/WS Pilot pair per 031–034/042 lineage, same resolved lock except
Skill treatment, `agent_turn_limit = 128`, finite attempts, queue-wait policy
and Infra-invalid retry. Queue wait/polling is external and consumes no model
turns. Reject any Case/Skill/request attempt to set model, provider, API
credentials or budgets.

Do not edit existing observed v1 protocol. Any removal of HPC hints from Case
instructions requires a Case-version bump and is reviewed/frozen separately;
the Dispatcher refactor itself leaves instruction bytes unchanged.

- [ ] **Step 2: Stop for explicit Pilot authorization**

No Pilot begins automatically after canaries.

- [ ] **Step 3: Run and validate Pilots**

Every run must use `bench-hpc -> HpcDispatcher -> SlurmDriver`; source scan and
RunRecord prove zero Case-ID branches and no ProcessDriver. Preserve
scientific failure separately from Infra invalidity.

- [ ] **Step 4: Build new release**

Do not rewrite old release. Recompute every component digest and require the
qualification receipt. 042 remains excluded from scientific Formal if its Case
G5–G12 are not independently closed; it may still supply an Infra Pilot.

- [ ] **Step 5: Run full verification**

Run: `.venv/bin/pytest -q -p no:cacheprovider tests`
Run: `.venv/bin/python scripts/infra/activate_v2.py --mode real --json`
Expected: zero unowned failures; all Dispatcher gates PASS; release digests
recompute.

- [ ] **Step 6: Delete legacy only after source/record audit**

Delete MatClaw controller, CASE_POLICIES, legacy gateway transport and eval
compatibility shims only after an exact source scan proves no production import
or historical reader depends on them. Remove their COPY/symlink instructions
from both controller Dockerfiles and update the run-identity test to assert the
new Dispatcher entry. Keep `scripts/ablation/transport/slurm_transport.py` as
the internal Slurm transport until a separately tested replacement exists;
keep all v1 RunRecord/result readers. Any additional live dependency is a STOP,
not authorization to expand deletion scope.

- [ ] **Step 7: Commit in two parts**

Commit release/protocol first; review. Commit legacy deletion separately so it
can be reverted without invalidating the release evidence.

```bash
git add experiments/skill-ablation-v2/protocol.yaml \
  dftworld_bench/experiments/release_builder.py releases/hpc-dispatcher-v1.json \
  scripts/infra/activate_v2.py tests/experiments/test_release_reproducibility.py \
  tests/hpc/test_all_hpc_cases_v2.py
git commit -m "release: freeze hpc dispatcher v1"

git add base-env-build/matclaw-cips-controller/Dockerfile \
  base-env-build/ai2kit-controller/Dockerfile tests/hpc/test_eval_run_identity.py
git add -u scripts/matclaw_hpc_controller.py scripts/matclaw_hpc_gateway.py \
  tests/test_matclaw_hpc_controller.py tests/test_matclaw_hpc_gateway.py
git commit -m "refactor(hpc): retire legacy MatClaw control path"
```

---

## Adversarial Traceability

| Risk | Owning task | Required proof |
|---|---:|---|
| Same operation physically submitted twice | 3, 7 | operation-attempt idempotency + crash test |
| Candidate swaps input bytes after validation | 2, 6, 10 | content-addressed sealed snapshot + race test |
| Crash after `sbatch` loses scheduler ID | 3, 10 | fsynced intent + exact-marker adoption, no resubmit |
| Agent creates hidden scientific retry | 3 | attempt increment required and recorded |
| Agent skips attempt numbers or runs attempts concurrently | 2, 3 | monotonic/terminal predecessor gate |
| v2 silently changes frozen v1 CLI | 3, 12 | protocol-version compatibility fixtures |
| Candidate injects SBATCH/custom flags | 2, 6 | schema and wrapper rejection |
| Candidate launches nested Apptainer with new binds | 6 | runtime executable/bind denial fixture |
| Candidate reaches personal HOME/old solution | 6, 11 | behavioral containment probe |
| Credential/site config leaks | 5, 7 | Candidate env/bundle/RunRecord secret scan |
| Resource ceiling bypass | 2, 5, 6 | typed request + SiteProfile clamp/reject |
| ProcessDriver used in Formal | 4, 10, 12 | admission and release rejection |
| Queue wait consumes model turns | 7 | BudgetLedger/Coordinator test |
| Infra retry hides scientific failure | 7, 8 | separate transport vs Agent attempts |
| Remote output silently repairs submission | 9 | explicit fetch and Verifier test |
| Remote output symlink leaks host/other-run bytes | 9 | trusted remote lstat/realpath rejection |
| Fetch destination symlink/partial copy corrupts submission | 9, 10 | no-follow staging + fsync + atomic no-overwrite publish |
| Legitimate archive is rejected or auto-extracted | 9 | opaque regular-file transfer fixture |
| Orphan jobs survive run | 9, 11 | settlement + real sacct evidence |
| Settlement cancels unrelated <site-user> jobs | 9, 11 | exact ledger job IDs only |
| Case-specific dispatcher branch | 7, 12 | source scan + five-lineage matrix |
| Mutable/missing site/runtime identity | 5, 11, 12 | digest/receipt/release preflight |
| Bundled dependency Skill reintroduces raw SSH/site hints | 8 | recursive bundled Skill source scan |
| Case instruction changes are hidden in Infra refactor | 12 | instruction digest/version gate |
| Legacy deletion breaks old evidence | 12 | historical reader fixture |

## Completion Definition

The simplification is complete only when:

- a clean baseline and all R3 findings are genuinely closed;
- all Agent-facing HPC operations enter HpcDispatcher;
- ExecutionRequest v2 and operation-attempt lineage are frozen;
- SlurmDriver is the sole qualified Formal driver;
- ProcessDriver/fake scheduler are impossible in Formal;
- authorized the site canaries pass containment, CPU, CP2K and GPU gates;
- five-lineage Pilots use no Case-specific HPC code;
- explicit fetch, settlement and RunRecord evidence are complete;
- full suite and release recomputation pass;
- legacy code is deleted only after behavioral parity and historical audit.
