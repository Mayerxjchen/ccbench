# Case 034 Heterogeneous Resource Evaluation Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PAgent discover abstract CPU/GPU capabilities through `bench-hpc`, autonomously decompose the Case 034 workflow, and receive an independent 10-point resource-engineering score without binding scientific validity to hardware.

**Architecture:** Extend the existing `bench-hpc` v1 boundary rather than introducing raw SSH. A versioned Candidate-facing capability catalog names logical runtimes (`cpu-dft`, `gpu-ml`, `cpu-control`); a trusted Site Adapter maps those names to scheduler details. JobSpec v2 carries stage role, logical runtime, resources, and structured scientific workload counters. The gateway enforces aggregate budgets and writes trusted job records; the Case 034 evaluator computes resource score separately from the existing scientific result.

**Tech Stack:** Python 3.11+, dataclasses, JSON Schema 2020-12, PyYAML, jsonschema, pytest/unittest, trusted `bench-hpc` gateway, Slurm adapter/transport, OCI/Apptainer runtime digests.

## Global Constraints

- Normative design: `docs/superpowers/specs/2026-08-18-034-heterogeneous-resource-evaluation-design.md`.
- Candidate HPC surface is `bench-hpc` only. Never mount SSH material or expose `ssh`, `sbatch`, `squeue`, `sacct`, partition names, GRES syntax, module paths, accounts, or scratch paths.
- Preserve JobSpec v1 compatibility for Cases 031-033 while Case 034 moves to JobSpec v2. Do not silently infer v2 fields for a Case 034 job.
- Hidden CPU reference remains constructive evidence only. Never copy hidden reference coordinates, outputs, thresholds, or scripts into the Candidate bundle.
- Full reference ceilings: 5 AL rounds, 4 committee models, 190 DFT labels, 10,000 CP2K AIMD steps, 5,600,000 aggregate training model-steps, 2,500,000 aggregate exploration steps, and 5,000 validation MD steps.
- Aggregate resource ceilings: 20 concurrent jobs, 320 total jobs, 4,096 CPU-hours, 512 GPU-hours, 200 GiB storage, 48 hours per job, and 336 hours per run.
- Scientific validity and the 10-point resource score are independent. A scientifically valid all-CPU fallback remains `VALID_RESULT`.
- Queue wait is recorded but not charged as CPU/GPU usage and is not an efficiency score input.
- Every submit is idempotent. Failed/retried jobs count against job and resource budgets.
- Use trusted observed accounting at settlement. Candidate-declared workload undercount is a resource violation.
- No formal No-Skill/With-Skill ablation is run in this plan.
- Do not modify or commit unrelated dirty files, including `scripts/evidence/finalize_run.py`, `scripts/reference/finalize_032_evidence_cluster.sh`, or the untracked local `ai2kit/` reference.

---

### Task 1: Define the v2 capability, budget, and JobSpec contracts

**Files:**
- Create: `schemas/hpc-capabilities.schema.json`
- Create: `schemas/hpc-run-budget.schema.json`
- Modify: `schemas/hpc-job.schema.json`
- Modify: `schemas/resource-profile.schema.json`
- Create: `dftworld_bench/hpc/capabilities.py`
- Modify: `dftworld_bench/hpc/job.py`
- Create: `tests/hpc/test_capability_contract.py`
- Modify: `tests/hpc/test_job_contract.py`

**Interfaces:**
- Produces `CapabilityCatalog.from_dict(payload) -> CapabilityCatalog`.
- Produces `CapabilityCatalog.public_dict() -> dict[str, object]`, containing no site fields.
- Produces `CapabilityCatalog.validate_job(spec: JobSpec) -> None`.
- Extends `JobSpec` with `stage_role`, `capability_id`, `runtime_id`, and `workload`.
- Keeps JobSpec v1 readable; v2 requires every new field.

- [ ] **Step 1: Write failing capability privacy and compatibility tests**

Add tests that construct this logical catalog:

```python
CATALOG = {
    "contract_version": "hpc-capabilities/v2",
    "capability_classes": [
        {
            "id": "cpu-dft",
            "kind": "cpu",
            "runtime_ids": ["cp2k-blyp-d3"],
            "limits": {"max_cpus": 16, "max_memory_gb": 32,
                       "max_gpus": 0, "max_walltime_minutes": 2880},
        },
        {
            "id": "gpu-ml",
            "kind": "accelerator",
            "accelerator": {"api": "cuda", "devices_per_job": 1},
            "runtime_ids": ["deepmd-cuda", "lammps-deepmd-cuda"],
            "limits": {"max_cpus": 8, "max_memory_gb": 32,
                       "max_gpus": 1, "max_walltime_minutes": 1440},
        },
    ],
    "budget": {
        "max_concurrent_jobs": 20, "max_total_jobs": 320,
        "max_cpu_hours": 4096, "max_gpu_hours": 512,
        "max_storage_bytes": 214748364800, "max_run_walltime_hours": 336,
    },
}
```

Assert that `public_dict()` recursively excludes `partition`, `queue`, `gres`,
`module`, `account`, `ssh_alias`, `scratch`, and absolute paths. Assert that
`gpu-ml + cp2k-blyp-d3`, `cpu-dft + gpus=1`, and a runtime digest not registered
under `runtime_id` are rejected.

- [ ] **Step 2: Write failing JobSpec v2 tests**

Use this valid payload and pin its round trip:

```yaml
schema_version: 2
idempotency_key: run-034-r1-model-0
stage_role: deepmd_train
capability_id: gpu-ml
runtime_id: deepmd-cuda
runtime: deepmd-cuda@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
command: [python, -m, workflow.train]
resources: {cpus: 4, memory_gb: 16, gpus: 1, walltime_minutes: 240}
workload:
  al_round: 1
  committee_models: 1
  training_model_steps: 100000
  dft_labels: 0
  cp2k_aimd_steps: 0
  exploration_md_steps: 0
  validation_md_steps: 0
inputs: [input.json, data/]
outputs: [model/, logs/]
```

Add negative tests for missing fields, unknown stage role, negative counters,
`al_round > 5`, shell-string command, site fields, and runtime digest drift.
Keep one existing v1 fixture green.

- [ ] **Step 3: Run the contract tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/hpc/test_capability_contract.py tests/hpc/test_job_contract.py -q
```

Expected: failures for missing schemas/classes and rejected v2 fields.

- [ ] **Step 4: Implement schemas and immutable Python models**

Define:

```python
STAGE_ROLES = (
    "structure_prepare", "cp2k_geopt", "cp2k_aimd", "deepmd_train",
    "lammps_explore", "screen_candidates", "cp2k_label",
    "validate_model", "other",
)

@dataclass(frozen=True)
class Workload:
    al_round: int = 0
    committee_models: int = 0
    training_model_steps: int = 0
    dft_labels: int = 0
    cp2k_aimd_steps: int = 0
    exploration_md_steps: int = 0
    validation_md_steps: int = 0
```

Represent runtime identities in the trusted catalog as logical ID plus full
digest. Reject any v2 runtime whose digest does not exactly match the selected
catalog runtime. Implement schema `oneOf` branches for v1 and v2 rather than
weakening the v2 required list.

- [ ] **Step 5: Run tests and verify GREEN**

Run the command from Step 3. Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add schemas/hpc-capabilities.schema.json schemas/hpc-run-budget.schema.json \
  schemas/hpc-job.schema.json schemas/resource-profile.schema.json \
  dftworld_bench/hpc/capabilities.py dftworld_bench/hpc/job.py \
  tests/hpc/test_capability_contract.py tests/hpc/test_job_contract.py
git commit -m "hpc: define logical capabilities and jobspec v2"
```

---

### Task 2: Enforce aggregate resource and scientific budgets in the gateway

**Files:**
- Create: `dftworld_bench/hpc/accounting.py`
- Modify: `dftworld_bench/hpc/gateway.py`
- Modify: `dftworld_bench/hpc/adapters/base.py`
- Modify: `tests/hpc/test_gateway.py`
- Create: `tests/hpc/test_accounting.py`

**Interfaces:**
- Produces `BudgetLedger.reserve(run_id, spec) -> Reservation`.
- Produces `BudgetLedger.settle(run_id, job_id, observed) -> JobAudit`.
- Produces `BudgetLedger.public_usage(run_id) -> dict`.
- Produces `BudgetLedger.trusted_record(run_id) -> dict`.
- Adds trusted adapter method `account(job_id) -> dict[str, object]`.

- [ ] **Step 1: Write failing reservation and settlement tests**

Cover:

- 21st concurrent job rejected;
- 321st total job rejected;
- requested upper-bound CPU/GPU hours reserved before submit;
- duplicate idempotency key does not double-charge;
- failed and cancelled jobs remain counted;
- settlement replaces reservation with observed CPU/GPU hours;
- queue wait is recorded but not charged;
- 191st DFT label, round 6, fifth committee member, 10,001st AIMD step,
  5,600,001st training model-step, and 2,500,001st exploration step rejected;
- observed workload greater than declared sets `resource_violation=true`;
- `public_usage()` omits site identifiers and returns remaining budget.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/hpc/test_accounting.py tests/hpc/test_gateway.py -q
```

- [ ] **Step 3: Implement the ledger**

Use integer seconds internally to avoid floating-point quota drift:

```python
requested_cpu_seconds = spec.resources.cpus * spec.resources.walltime_minutes * 60
requested_gpu_seconds = spec.resources.gpus * spec.resources.walltime_minutes * 60
```

`reserve()` is atomic: validate every resource/workload ceiling first, then
record the reservation. `settle()` is idempotent and requires trusted adapter
accounting containing scheduler state, exit code, elapsed, allocated CPU/GPU,
CPU time, GPU time when available, and observed workload.

- [ ] **Step 4: Integrate the ledger with Gateway**

The submit order is:

```python
self.authorize(token, run_id, "submit")
spec_obj = JobSpec.from_payload(spec)
self._catalog.validate_job(spec_obj)
reservation = self._ledger.reserve(run_id, spec_obj)
try:
    result = self._adapter.submit(spec_obj.to_dict(), run_id=run_id)
except Exception:
    self._ledger.release_unsubmitted(reservation)
    raise
self._ledger.bind_job(reservation, result["job_id"])
```

Do not charge duplicates twice. Trigger settlement on terminal `status`,
`fetch`, `cancel`, and final gateway shutdown/reconciliation.

- [ ] **Step 5: Run tests and verify GREEN**

Run Step 2 plus:

```bash
uv run --extra dev pytest tests/hpc/test_client.py tests/hpc/test_gateway.py -q
```

- [ ] **Step 6: Commit**

```bash
git add dftworld_bench/hpc/accounting.py dftworld_bench/hpc/gateway.py \
  dftworld_bench/hpc/adapters/base.py tests/hpc/test_accounting.py \
  tests/hpc/test_gateway.py
git commit -m "hpc: enforce aggregate run and science budgets"
```

---

### Task 3: Map logical capabilities through the trusted Slurm Site Adapter

**Files:**
- Modify: `schemas/platform-profile.schema.json`
- Modify: `schemas/site-config.schema.json`
- Modify: `site-configs/example-slurm.yaml`
- Modify: `dftworld_bench/hpc/adapters/slurm.py`
- Modify: `scripts/ablation/transport/slurm_transport.py`
- Modify: `tests/hpc/test_slurm_adapter.py`
- Create: `tests/hpc/test_site_capability_privacy.py`

**Interfaces:**
- Site config privately maps `capability_id -> partition/gres/account/runtime`.
- `SlurmAdapter.capabilities()` returns only the public catalog.
- `SlurmAdapter.render()` selects directives exclusively from the private
  binding identified by `capability_id`.
- `SshSlurmTransport.account(job_id) -> dict` parses trusted `sacct` fields.

- [ ] **Step 1: Write two-site equivalence tests**

Create two valid site configs whose private bindings differ:

```yaml
# site A private binding
capability_id: gpu-ml
partition: gpu
gres: gpu:1

# site B private binding
capability_id: gpu-ml
partition: accelerators-a100
gres: gpu:a100:1
```

Assert identical Candidate-facing `capabilities()` output and different typed
`SubmitOpts`. Assert the public JSON contains neither site's private values.

- [ ] **Step 2: Write failing compatibility and accounting tests**

Assert:

- `cpu-dft` renders CPU partition with no GRES;
- `gpu-ml` renders one GPU using site-owned syntax;
- Candidate environment cannot override partition, GRES, modules, account,
  scratch, or runtime image;
- selected runtime digest comes from the capability binding and must equal the
  JobSpec digest;
- `account()` parses parent-job `State,ExitCode,ElapsedRaw,TotalCPURaw,AllocTRES`
  and never accepts a step row as the parent;
- unavailable GPU accounting is explicit `null`, never fabricated zero.

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/hpc/test_slurm_adapter.py \
  tests/hpc/test_site_capability_privacy.py -q
```

- [ ] **Step 4: Extend private site schemas and adapter mapping**

Keep public capability fields separate from bindings:

```yaml
platform_profile:
  public_capabilities:
    - id: cpu-dft
      kind: cpu
      runtime_ids: [cp2k-blyp-d3]
      limits: {max_cpus: 16, max_memory_gb: 32, max_gpus: 0, max_walltime_minutes: 2880}
    - id: gpu-ml
      kind: accelerator
      accelerator: {api: cuda, devices_per_job: 1}
      runtime_ids: [deepmd-cuda, lammps-deepmd-cuda]
      limits: {max_cpus: 8, max_memory_gb: 32, max_gpus: 1, max_walltime_minutes: 1440}
  bindings:
    cpu-dft:
      partition: cpu
      gres: null
      runtimes:
        cp2k-blyp-d3: cp2k-blyp-d3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    gpu-ml:
      partition: gpu
      gres: gpu:1
      runtimes:
        deepmd-cuda: deepmd-cuda@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
        lammps-deepmd-cuda: lammps-deepmd-cuda@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
```

The example configuration uses valid 64-hex demonstration digests. Production
site configuration receives real qualified digests in Task 10.

- [ ] **Step 5: Implement trusted accounting**

Add one bounded, parent-row-anchored accounting query to the transport. Return
raw and parsed values so the immutable audit retains evidence. Failures are
`UNKNOWN`/missing evidence, not zero usage.

- [ ] **Step 6: Run tests and verify GREEN**

Run Step 3 and `tests/hpc/test_process_conformance.py`.

- [ ] **Step 7: Commit**

```bash
git add schemas/platform-profile.schema.json schemas/site-config.schema.json \
  site-configs/example-slurm.yaml dftworld_bench/hpc/adapters/slurm.py \
  scripts/ablation/transport/slurm_transport.py \
  tests/hpc/test_slurm_adapter.py tests/hpc/test_site_capability_privacy.py
git commit -m "hpc: map logical capabilities through site adapters"
```

---

### Task 4: Upgrade the client, conformance suite, and Candidate skill surface

**Files:**
- Modify: `dftworld_bench/hpc/__main__.py`
- Modify: `dftworld_bench/hpc/client.py`
- Modify: `dftworld_bench/hpc/conformance.py`
- Modify: `dftworld_bench/hpc/adapters/process_test.py`
- Modify: `base-env-build/skills/hpc-submit/SKILL.md`
- Modify: `base-env-build/skills/hpc-submit/references/running.md`
- Modify: `base-env-build/ai2kit-controller/Dockerfile`
- Modify: `tests/hpc/test_client.py`
- Modify: `tests/hpc/test_process_conformance.py`
- Create: `tests/hpc/test_candidate_hpc_surface.py`

**Interfaces:**
- Candidate CLI commands remain unchanged.
- `bench-hpc capabilities` emits v2 public JSON.
- `bench-hpc usage` emits consumed/reserved/remaining budgets and no site data.
- Benchmark `hpc-submit` skill teaches the capability contract, not SSH/Slurm.

- [ ] **Step 1: Write failing CLI and image-surface tests**

Assert that the controller image contains `bench-hpc` and does not contain or
copy `ssh`, `rsess`, `sbatch`, `squeue`, scheduler profiles, or credential
files. Assert that capability/usage JSON round-trips without stderr prose.

- [ ] **Step 2: Write failing conformance v2 tests**

The process adapter must advertise `cpu-control`; test jobs use JobSpec v2 and
verify compatibility, workload accounting, idempotency, settlement, and
trusted record creation. Conformance remains adapter-agnostic.

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/hpc/test_client.py \
  tests/hpc/test_process_conformance.py tests/hpc/test_candidate_hpc_surface.py -q
```

- [ ] **Step 4: Replace benchmark-image raw scheduler guidance**

In the bundled `hpc-submit` skill, make the Candidate procedure:

```text
1. bench-hpc capabilities
2. choose capability_id/runtime_id/resources/workload
3. bench-hpc submit job.yaml
4. bench-hpc status/logs
5. bench-hpc fetch after SUCCEEDED
6. bench-hpc usage before additional work
```

Remove benchmark-image instructions to discover partition/GRES/module syntax,
open SSH sessions, or call scheduler commands. Generic scientific reasoning
about CPU/GPU suitability remains.

- [ ] **Step 5: Implement client/conformance updates and rebuild the controller**

Copy only the installed `bench-hpc` client package/entrypoint into the
controller image. Do not copy Site Adapter code or transport credentials into
the Candidate layer. Record the rebuilt image identity through the existing
image build mechanism.

- [ ] **Step 6: Run tests and verify GREEN**

Run Step 3 plus the controller-image contract tests used by Cases 031-034.

- [ ] **Step 7: Commit**

```bash
git add dftworld_bench/hpc/__main__.py dftworld_bench/hpc/client.py \
  dftworld_bench/hpc/conformance.py dftworld_bench/hpc/adapters/process_test.py \
  base-env-build/skills/hpc-submit/SKILL.md \
  base-env-build/skills/hpc-submit/references/running.md \
  base-env-build/ai2kit-controller/Dockerfile tests/hpc/test_client.py \
  tests/hpc/test_process_conformance.py tests/hpc/test_candidate_hpc_surface.py
git commit -m "hpc: expose capability-driven candidate workflow"
```

---

### Task 5: Persist trusted HPC job records in the immutable Run Record

**Files:**
- Modify: `schemas/run-record.schema.json`
- Modify: `dftworld_bench/contracts/run_record.py`
- Modify: `dftworld_bench/core/harness.py`
- Modify: `dftworld_bench/core/run_store.py`
- Modify: `tests/core/test_run_store.py`
- Create: `tests/hpc/test_run_record_accounting.py`

**Interfaces:**
- Run Record v2 adds `hpc_jobs`, `hpc_usage`, and `resource_evaluation`.
- Existing Run Record v1 remains readable.
- Harness accepts a trusted `hpc_audit_provider(run_id) -> dict` after
  settlement; Candidate output is never used as the trusted source.

- [ ] **Step 1: Write failing v2 Run Record tests**

Pin this shape:

```json
{
  "hpc_jobs": [{
    "job_id": "job-0001",
    "stage_role": "deepmd_train",
    "capability_id": "gpu-ml",
    "runtime_id": "deepmd-cuda",
    "requested": {"cpus": 4, "gpus": 1},
    "allocated": {"cpus": 4, "gpus": 1},
    "state": "SUCCEEDED",
    "exit_code": "0:0",
    "usage": {"cpu_hours": 0.8, "gpu_hours": 0.2},
    "declared_workload": {},
    "observed_workload": {},
    "input_digest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "output_digest": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "fallback_from": null
  }],
  "hpc_usage": {},
  "resource_evaluation": {"score": 10, "max_score": 10, "criteria": {}}
}
```

Reject Candidate-authored audit input, missing settlement, secret/site fields,
and resource scores outside 0-10. Verify a v1 record still loads.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/core/test_run_store.py \
  tests/hpc/test_run_record_accounting.py -q
```

- [ ] **Step 3: Implement Run Record v2 and harness injection**

The harness settles/revokes the HPC capability before Candidate destruction,
then asks the trusted gateway for the audit. `BenchmarkResult` remains the
scientific/top-level classification; do not add resource score to its validity
logic.

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2 plus `tests/core/` and `tests/contracts/`.

- [ ] **Step 5: Commit**

```bash
git add schemas/run-record.schema.json dftworld_bench/contracts/run_record.py \
  dftworld_bench/core/harness.py dftworld_bench/core/run_store.py \
  tests/core/test_run_store.py tests/hpc/test_run_record_accounting.py
git commit -m "records: persist trusted hpc jobs and usage"
```

---

### Task 6: Migrate Case 034 profiles to full-reference ceilings and logical runtimes

**Files:**
- Modify: `034-ai2kit-water64-end-to-end-potential/task.toml`
- Modify: `034-ai2kit-water64-end-to-end-potential/profiles/platform.yaml`
- Modify: `034-ai2kit-water64-end-to-end-potential/profiles/resource.yaml`
- Modify: `034-ai2kit-water64-end-to-end-potential/profiles/formal.yaml`
- Modify: `034-ai2kit-water64-end-to-end-potential/profiles/smoke.yaml`
- Modify: `034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json`
- Modify: `tests/hpc/test_034_integration.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test_reference_contract.py`

**Interfaces:**
- Case 034 requires `hpc-execution/v2` and logical capabilities
  `cpu-dft`, `gpu-ml`, `cpu-control`, `artifact-fetch`.
- Resource profile exposes the approved aggregate and science budgets.
- Runtime lock contains four logical, digest-pinned runtime entries.

- [ ] **Step 1: Replace old profile assertions with failing v2 assertions**

Assert exact ceilings:

```python
assert budget == {
    "max_concurrent_jobs": 20,
    "max_total_jobs": 320,
    "max_cpu_hours": 4096,
    "max_gpu_hours": 512,
    "max_storage_bytes": 214748364800,
    "max_walltime_minutes_per_job": 2880,
    "max_run_walltime_hours": 336,
}
assert science["max_dft_labels"] == 190
assert science["max_cp2k_aimd_steps"] == 10000
assert science["max_committee_models"] == 4
assert science["max_training_model_steps"] == 5600000
assert science["max_exploration_md_steps"] == 2500000
assert science["max_active_learning_rounds"] == 5
```

Assert there is no CPU-only Candidate lock and no `default_queue`, `partition`,
`gres`, module path, SSH name, or scratch path in Candidate-facing profiles.

- [ ] **Step 2: Write runtime-lock tests before editing the lock**

Require exactly:

```text
ai2kit-control
cp2k-blyp-d3
deepmd-cuda
lammps-deepmd-cuda
```

Each entry carries a full runtime digest, architecture, qualification evidence
digest, software versions, and compatible capability IDs. The hidden CPU
reference identity is recorded under `constructive_reference`, not as a
Candidate runtime.

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/hpc/test_034_integration.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_reference_contract.py -q
```

- [ ] **Step 4: Rewrite the Case 034 profiles**

Keep the public instruction hardware-neutral. Change the agent timeout to the
approved 336-hour run ceiling only in the HPC lifecycle configuration; verifier
timeout remains 10,800 seconds. Smoke remains non-scientific and receives a
separate small aggregate budget.

Do not invent runtime digests. Until Task 10 qualifies a runtime, represent it
as `status: pending_qualification` and keep `benchmark_valid=false`; schema and
tests must reject release while any required runtime is pending.

- [ ] **Step 5: Run tests and verify GREEN for construction state**

Run Step 3. Expected: profile contracts pass; release-readiness test remains
explicitly blocked only by pending runtime qualification.

- [ ] **Step 6: Commit**

```bash
git add 034-ai2kit-water64-end-to-end-potential/task.toml \
  034-ai2kit-water64-end-to-end-potential/profiles \
  034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json \
  tests/hpc/test_034_integration.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_reference_contract.py
git commit -m "034: declare heterogeneous capabilities and full ceilings"
```

---

### Task 7: Implement the independent 10-point resource-engineering evaluator

**Files:**
- Create: `dftworld_bench/hpc/resource_scoring.py`
- Create: `034-ai2kit-water64-end-to-end-potential/profiles/resource-scoring.yaml`
- Modify: `dftworld_bench/core/harness.py`
- Create: `tests/hpc/test_resource_scoring.py`
- Create: `034-ai2kit-water64-end-to-end-potential/tests/test_resource_scoring_contract.py`

**Interfaces:**
- Produces `score_resource_engineering(policy, audit) -> ResourceEvaluation`.
- `ResourceEvaluation.score` is 0-10 and never changes `BenchmarkResult`.
- Inputs are trusted audit records only.

- [ ] **Step 1: Write the scoring matrix tests**

Cover exact points:

```yaml
capability_discovery: 2
workflow_decomposition: 2
deepmd_accelerator_use: 2
lammps_accelerator_use: 2
cp2k_resource_choice: 1
budget_fallback_provenance: 1
```

Required scenarios:

- scientifically passing heterogeneous run scores 10/10;
- scientifically passing all-CPU run remains `VALID_RESULT` and scores no more
  than discovery/decomposition/provenance points;
- compatible GPU advertised but unused loses accelerator points;
- failed GPU compatibility probe linked by `fallback_from` plus successful CPU
  fallback remains eligible for accelerator decision points;
- Candidate prose claiming GPU with no trusted allocation scores zero GPU
  points;
- GPU allocated to CPU CP2K loses CP2K choice point;
- blind retries or workload under-declaration lose provenance point;
- `SCIENTIFIC_FAIL` and `PASS` with identical audit receive identical resource
  scores.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run --extra dev pytest tests/hpc/test_resource_scoring.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_resource_scoring_contract.py -q
```

- [ ] **Step 3: Implement pure scoring predicates**

Keep each criterion a pure function returning points plus evidence/reason:

```python
@dataclass(frozen=True)
class CriterionResult:
    points: int
    maximum: int
    reason: str
    job_ids: tuple[str, ...]

@dataclass(frozen=True)
class ResourceEvaluation:
    score: int
    max_score: int
    criteria: dict[str, CriterionResult]
```

Do not parse Candidate reports for trusted allocation. Capability discovery is
proven by the gateway's capability-read event before first submit.

- [ ] **Step 4: Integrate after scientific verification**

The harness computes resource evaluation from the settled audit and writes it
to Run Record v2. It does not rewrite `result_class`, `failure_code`, reward, or
scientific denominator flags.

- [ ] **Step 5: Run tests and verify GREEN**

Run Step 2 plus `tests/core/`.

- [ ] **Step 6: Commit**

```bash
git add dftworld_bench/hpc/resource_scoring.py \
  034-ai2kit-water64-end-to-end-potential/profiles/resource-scoring.yaml \
  dftworld_bench/core/harness.py tests/hpc/test_resource_scoring.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_resource_scoring_contract.py
git commit -m "034: score heterogeneous resource engineering independently"
```

---

### Task 8: Require auditable autonomous resource decisions without prescribing them

**Files:**
- Modify: `034-ai2kit-water64-end-to-end-potential/instruction.md`
- Modify: `034-ai2kit-water64-end-to-end-potential/CONTRACT.md`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/verifier.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test_outputs.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test_docs_contract.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py`

**Interfaces:**
- Candidate final submission includes `final/resource-decisions.json` as
  Candidate-authored reasoning/provenance, but trusted score uses gateway audit.
- Scientific verifier checks the file is present and internally linked to job
  IDs; it does not trust resource allocation claims in the file.

- [ ] **Step 1: Write failing autonomy and leakage tests**

Require a resource decision record with:

```json
{
  "capability_contract": "hpc-capabilities/v2",
  "stages": [{
    "stage_role": "deepmd_train",
    "selected_capability": "gpu-ml",
    "job_ids": ["job-0003"],
    "reason": "CUDA-compatible training capability",
    "fallback_from": null
  }],
  "stopping_decision": {
    "rounds_completed": 3,
    "reason": "held-out and uncertainty evidence satisfied"
  }
}
```

Assert public files do not mention logical answers such as “DeepMD must use
gpu-ml” or “CP2K must use cpu-dft”, and do not contain hidden job IDs,
reference structures, partition/module names, or site paths.

- [ ] **Step 2: Add scientific stopping-policy tests**

The verifier must accept fewer than five rounds only when the submission has
real iterative growth and the existing held-out/uncertainty/physical gates
pass. It must reject “budget exhausted” as the sole stopping rationale and
reject fabricated round counts that disagree with artifacts.

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run --extra dev pytest \
  034-ai2kit-water64-end-to-end-potential/tests/test_outputs.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_docs_contract.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py -q
```

- [ ] **Step 4: Update the instruction and verifier boundary**

Instruction wording remains abstract:

```text
Discover the available logical compute capabilities through bench-hpc. Decide
how to decompose and resource each scientific stage within the declared run
budget. Record the evidence and rationale for resource selection, fallback,
and active-learning stopping. Site-specific scheduler access is unavailable.
```

Do not publish the scoring rubric or preferred stage mapping in the Candidate
instruction.

- [ ] **Step 5: Run tests and verify GREEN**

Run Step 3 plus the full Case 034 non-runtime test suite.

- [ ] **Step 6: Commit**

```bash
git add 034-ai2kit-water64-end-to-end-potential/instruction.md \
  034-ai2kit-water64-end-to-end-potential/CONTRACT.md \
  034-ai2kit-water64-end-to-end-potential/tests/verifier.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_outputs.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_docs_contract.py \
  034-ai2kit-water64-end-to-end-potential/tests/test_pagent_autonomy_contract.py
git commit -m "034: require auditable autonomous resource decisions"
```

---

### Task 9: Regenerate immutable locks, manifests, and mutation coverage

**Files:**
- Modify: `scripts/ablation/make_common_lock.sh`
- Modify: `scripts/ablation/verify_lock.sh`
- Modify: `scripts/ablation/test_autonomous_lock.py`
- Modify: `034-ai2kit-water64-end-to-end-potential/ablation/locks/common_ablation_lock.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/ablation/locks/agent_lock.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/evaluator-manifest.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/ablation/README.md`
- Create: `tests/hpc/test_034_resource_mutations.py`
- Modify: `docs/architecture/HPC-CONTRACT-v1.md`
- Create: `docs/architecture/HPC-CONTRACT-v2.md`

**Interfaces:**
- Common lock freezes capability schema, budgets, logical runtimes, Site
  Adapter implementation digest, scoring policy, and Candidate image.
- Agent lock differs only by skill presence/content digest.
- Mutation suite proves each resource-scoring and privacy contract can fail.

- [ ] **Step 1: Write mutation tests before regenerating locks**

Mutations must turn RED when they:

- restore `gpus=0` CPU-only policy;
- expose partition/GRES/module/SSH/scratch in capabilities;
- drop runtime/capability compatibility;
- omit workload counters;
- stop enforcing label/training/exploration ceilings;
- remove trusted allocated-GPU evidence;
- award GPU points from Candidate prose;
- let resource score change scientific validity;
- remove fallback linkage;
- make No-Skill and With-Skill resource profiles differ.

- [ ] **Step 2: Run mutation tests and verify they are RED against each mutation**

```bash
uv run --extra dev pytest tests/hpc/test_034_resource_mutations.py -q
```

- [ ] **Step 3: Update deterministic lock generation**

Hash only tracked source files and content-addressed runtime evidence. Site
config contents and tokens are forbidden; include only the secret-free Site
Adapter configuration digest. Rebuilding twice must be byte-identical.

- [ ] **Step 4: Regenerate manifests and locks**

Use the repository's existing lock/manifest generators. Run them twice and
compare bytes. Update the evaluator manifest only after every changed evaluator
file is final.

- [ ] **Step 5: Run contract, mutation, and leakage audits**

```bash
uv run --extra dev pytest tests/hpc tests/contracts tests/docs -q
uv run --extra dev pytest 034-ai2kit-water64-end-to-end-potential/tests -q
python 034-ai2kit-water64-end-to-end-potential/tools/leak_audit.py
bash scripts/ablation/verify_lock.sh 034-ai2kit-water64-end-to-end-potential
```

Expected: all available-environment tests pass; dependency-specific skips are
explicit, and no hidden/site artifact appears in the Candidate bundle.

- [ ] **Step 6: Commit**

```bash
git add scripts/ablation/make_common_lock.sh scripts/ablation/verify_lock.sh \
  scripts/ablation/test_autonomous_lock.py \
  034-ai2kit-water64-end-to-end-potential/ablation \
  034-ai2kit-water64-end-to-end-potential/evaluator-manifest.json \
  tests/hpc/test_034_resource_mutations.py \
  docs/architecture/HPC-CONTRACT-v1.md docs/architecture/HPC-CONTRACT-v2.md
git commit -m "034: freeze heterogeneous resource evaluation contracts"
```

---

### Task 10: Prove fake-adapter end-to-end behavior and qualify the real CPU/GPU runtimes

**Files:**
- Create: `tests/hpc/test_034_heterogeneous_e2e.py`
- Create: `scripts/hpc/qualify_034_runtime.py`
- Create: `scripts/hpc/qualify_034_site.sh`
- Modify: `034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/benchmark_valid.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/VALIDATION.json`
- Create: `034-ai2kit-water64-end-to-end-potential/reference/runtime-evidence/heterogeneous/README.md`
- Modify: `releases/ablation-ready-v0.json`

**Interfaces:**
- Fake adapter demonstrates the entire capability -> submit -> settle -> fetch
  -> scientific result -> resource score path.
- Qualification script emits one machine-readable record per logical runtime.
- Real-site smoke uses `bench-hpc`; no Candidate raw SSH is permitted.

- [ ] **Step 1: Write the fake heterogeneous end-to-end test**

Simulate:

1. one capability read;
2. CPU structure preparation;
3. CPU CP2K AIMD;
4. two GPU DeepMD model jobs;
5. GPU LAMMPS exploration;
6. CPU CP2K labels;
7. successful validation;
8. settled Run Record with scientific PASS and resource score 10/10.

Add all-CPU and GPU-incompatible fallback variants. Both remain valid; scores
differ exactly according to policy.

- [ ] **Step 2: Run fake end-to-end and verify RED, then implement missing glue**

```bash
uv run --extra dev pytest tests/hpc/test_034_heterogeneous_e2e.py -q
```

Continue only when all fake variants pass deterministically.

- [ ] **Step 3: Implement runtime qualification scripts**

Each runtime qualification records:

- image digest and architecture;
- program/package versions;
- CPU/GPU allocation observed by trusted accounting;
- accelerator backend visibility where applicable;
- minimal functional smoke:
  - `ai2kit-control`: CLI/import/data round trip;
  - `cp2k-blyp-d3`: bounded CP2K ENERGY/OT calculation plus asset hashes;
  - `deepmd-cuda`: bounded train/freeze/test with CUDA backend evidence;
  - `lammps-deepmd-cuda`: plugin load, `pair_style deepmd`, bounded GPU run;
- output digests and terminal scheduler evidence.

The scripts default to dry-run and require a separate explicit gate for each
real submit/fetch pair. They never contain site partition/module/path values;
the Site Adapter supplies them.

- [ ] **Step 4: Run shared conformance with fake/process adapters**

```bash
uv run --extra dev pytest tests/hpc -q
python -m dftworld_bench.hpc.conformance
```

Do not connect to the real site until this is green and code-reviewed.

- [ ] **Step 5: Review and separately authorize bounded real-site smoke**

Run only:

- one `cpu-control` smoke;
- one `cpu-dft` smoke;
- one `deepmd-cuda` smoke;
- one `lammps-deepmd-cuda` smoke.

Each follows submit -> terminal settlement -> fetch with distinct idempotency
keys. `UNKNOWN`, incomplete accounting, digest drift, or possible duplicate
submission stops the sequence. No scientific production workflow or formal
ablation is authorized by this step.

- [ ] **Step 6: Freeze real runtime identities and re-run release gates**

Replace `pending_qualification` entries only from accepted evidence. Regenerate
the compute-runtime lock, evaluator manifest, common lock, and release manifest.
Set `benchmark_valid=true` only if every release criterion in the design spec
passes and no threshold remains draft/provisional.

- [ ] **Step 7: Run the final verification suite**

```bash
uv run --extra dev pytest tests/hpc tests/contracts tests/core tests/docs -q
uv run --extra dev pytest 034-ai2kit-water64-end-to-end-potential/tests -q
python scripts/ablation/readiness_audit.py
python 034-ai2kit-water64-end-to-end-potential/tools/leak_audit.py
bash scripts/ablation/verify_lock.sh 034-ai2kit-water64-end-to-end-potential
git diff --check
git status --short
```

Expected: all release gates pass, the only remaining untracked path is the
operator-owned `ai2kit/` reference if it is still present, and no formal
ablation run exists.

- [ ] **Step 8: Commit**

```bash
git add tests/hpc/test_034_heterogeneous_e2e.py scripts/hpc/qualify_034_runtime.py \
  scripts/hpc/qualify_034_site.sh \
  034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json \
  034-ai2kit-water64-end-to-end-potential/reference/runtime-evidence/heterogeneous \
  034-ai2kit-water64-end-to-end-potential/benchmark_valid.json \
  034-ai2kit-water64-end-to-end-potential/VALIDATION.json \
  releases/ablation-ready-v0.json
git commit -m "034: qualify heterogeneous hpc execution and close release gates"
```

---

## Plan Self-Review Checklist

- [ ] Every approved design requirement maps to a task.
- [ ] JobSpec v1 compatibility is tested; Case 034 requires v2.
- [ ] Site trivia is absent from Candidate capabilities, skills, profiles, and records.
- [ ] Aggregate resource and full-reference scientific ceilings are enforced.
- [ ] Under-declared workload is detected from trusted outputs.
- [ ] Scientific validity is independent of resource score.
- [ ] Hidden CPU reference remains sealed and constructive only.
- [ ] Runtime digests are accepted only after real qualification evidence.
- [ ] No formal ablation is run.
- [ ] Unrelated dirty files and the local `ai2kit/` reference are never committed.

## Execution Handoff

Implement tasks in order. Tasks 1-5 change shared infrastructure and require
review before Case 034 migration begins. Tasks 6-9 remain local/fake-adapter
work. Task 10 is the only task containing real-site actions, and each submit
and fetch remains separately authorized.
