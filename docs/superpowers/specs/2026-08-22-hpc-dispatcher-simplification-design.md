# HpcDispatcher Simplification Design

**Status:** Approved design baseline; implementation baseline pending  
**Date:** 2026-08-22  
**Scope:** DFTWorld real-HPC execution for all HPC Cases, initially the five
031–034/042 lineages.

## Goal

Provide one safe and explainable Formal HPC path:

```text
Case -> Candidate/Agent -> bench-hpc -> HpcDispatcher
     -> qualified real Slurm -> fetch -> Submission -> fresh Verifier
```

The Agent decides scientific workflow, command, resources, failure recovery,
and when to fetch. Trusted Infra validates and executes that decision on real
HPC without exposing evaluator credentials, personal HOME, hidden assets, or
site-specific configuration.

This design follows the decoupling principle demonstrated by OpenClaw and its
DPDispatcher skill, but does not add the DPDispatcher package to DFTWorld's
main dependency set. DFTWorld already owns stronger benchmark-specific
contracts for run identity, path containment, quotas, idempotency, evidence,
and recovery.

## First-Principles Invariants

1. **One declared treatment:** a counted NS/WS pair differs only in physical
   Skill availability; tool, prompt, site, request, runtime and budget identity
   remain equal.
2. **Untrusted decision, trusted execution:** Agent chooses scientific work;
   only trusted code owns credentials, site policy, scheduler flags, host paths
   and containment.
3. **Logical identity before scheduler identity:** `(run, operation, attempt)`
   is durable; scheduler job IDs are external provenance.
4. **Fail closed:** unknown request, path, runtime, output node, site state or
   qualification is invalid, never guessed.
5. **Scheduler success is not science:** only engine parsing and the independent
   Verifier establish scientific PASS/FAIL.
6. **Explicit retrieval:** remote existence never repairs Candidate submission.
7. **Trusted evidence:** state, usage, script and artifact evidence originate
   from Dispatcher/Driver, not Agent-authored reports.
8. **No hidden host reachability:** scientific commands see only the pinned
   runtime and run workspace.
9. **Versioned evolution:** protocol v2 cannot silently mutate frozen v1 CLI or
   historical records.
10. **Proof before deletion:** parity, qualification and Pilot evidence precede
    legacy removal.

## One Public Mental Model

External users need understand only four HPC concepts:

1. `bench-hpc`: the Agent-facing seven-operation CLI;
2. `ExecutionRequest`: the validated computation request;
3. `HpcDispatcher`: the sole trusted HPC entry;
4. `SiteProfile`: evaluator-private site configuration.

`Gateway`, `GatewayRuntime`, `JobSpec`, `SlurmAdapter`, SSH transport, audit
store, and scheduler job IDs remain implementation details during migration.

## Roles

### Case

Owns scientific objective, public inputs, runtime/capability requirements,
submission structure, and Verifier contract. It does not name HPC, Slurm,
partition, account, QOS, SSH, remote root, or exact resource answers.

### Candidate and Agent

The Candidate is a fresh temporary workstation. The Agent chooses local versus
remote stages, prepares files, builds requests, monitors jobs, diagnoses
failures, explicitly fetches results, and creates the final submission.

### hpc-submit Skill

Provides general operational knowledge: preflight, bounded resource requests,
monitoring, log diagnosis, checkpoint/restart, parser gating, and explicit
artifact retrieval. It never contains Case-specific scientific answers.

### bench-hpc

Stable CLI:

```text
bench-hpc help
bench-hpc capabilities
bench-hpc submit request.yaml
bench-hpc status OPERATION_ID
bench-hpc logs OPERATION_ID
bench-hpc fetch OPERATION_ID OUTPUT_DIR
bench-hpc cancel OPERATION_ID
bench-hpc usage
```

Legacy scheduler job-ID forms remain readable during migration but are not the
v2 public identity. Protocol v1 remains byte-stable for historical releases;
v2 is selected through a frozen protocol identity rather than silent CLI
behavior change.

### HpcDispatcher

The only trusted HPC entry. It validates requests, owns credentials and site
policy, stages files, submits to Slurm, normalizes state, fetches declared
artifacts, accounts resources, settles outstanding work, and appends evidence.

### Real HPC

Real the site Slurm, CPU/GPU, CP2K, DeePMD, LAMMPS, and digest-pinned
Apptainer/SIF runtimes. No fake scheduler produces Formal scientific scores.

### Verifier

Fresh local, networkless, non-root evaluation. It never fetches remote outputs
the Agent forgot to retrieve.

## ExecutionRequest v2

`ExecutionRequest` is the public name. Existing `JobSpec` may remain an
internal alias until the system is stable.

```yaml
schema_version: 2
operation_id: cp2k-round-01
attempt: 1
runtime: matclaw-cips@sha256:<64-hex>
command_argv: [bash, run.sh]
inputs:
  - {path: run.sh, sha256: "sha256:...", size_bytes: 814}
  - {path: input.inp, sha256: "sha256:...", size_bytes: 2401}
  - {path: structure.xyz, sha256: "sha256:...", size_bytes: 9612}
outputs: [cp2k.out, labels.xyz]
resources:
  cpus: 32
  memory_gb: 64
  gpus: 0
  walltime_minutes: 60
environment:
  PROFILE: paper
```

Rules:

- `operation_id` is the logical workflow-stage identity;
- `attempt` is an Agent-declared scientific retry number;
- `(run_id, operation_id, attempt)` is the idempotency key;
- attempt numbers start at 1, are monotonic and have no gaps;
- attempt `n+1` is admitted only after attempt `n` is scheduler-terminal;
- concurrent attempts for one operation are forbidden in v2;
- transport retries never increment `attempt`;
- scheduler `job_id` is internal provenance;
- command is argv, never a shell string;
- all paths are normalized run-relative paths;
- inputs are immutable, content-addressed snapshots; Dispatcher reopens them
  without following links and verifies size/hash before staging, so validation
  cannot race a later Candidate edit;
- runtime is immutable and capability-compatible;
- resources are clamped/rejected against frozen limits;
- environment keys are allowlisted;
- raw scheduler flags and DPDispatcher-style `custom_flags` are forbidden.

One logical operation may have multiple Agent attempts:

```text
cp2k-round-01
  attempt 1 -> Slurm 2812231 -> FAILED
  attempt 2 -> Slurm 2812398 -> COMPLETED
```

`status/logs/fetch/cancel` default to the latest attempt and can expose
attempt-specific details in structured JSON.

## HpcDispatcher Contract

```python
class HpcDispatcher:
    def validate(self, request: ExecutionRequest, run: RunContext) -> None: ...
    def submit(self, request: ExecutionRequest, run: RunContext) -> AttemptRecord: ...
    def status(self, run_id: str, operation_id: str, attempt: int | None = None) -> dict: ...
    def logs(self, run_id: str, operation_id: str, attempt: int | None = None) -> dict: ...
    def fetch(self, run_id: str, operation_id: str, destination: Path,
              attempt: int | None = None) -> ArtifactManifest: ...
    def cancel(self, run_id: str, operation_id: str,
               attempt: int | None = None) -> dict: ...
    def usage(self, run_id: str) -> dict: ...
    def settle(self, run_id: str) -> SettlementReport: ...
```

The façade initially composes existing Gateway/GatewayRuntime/JobSpec/
SlurmAdapter/EventStore implementations without behavior changes.

## Driver Boundary

Only two drivers are required:

```text
SlurmDriver    Formal, real HPC
ProcessDriver  CI and fault injection only
```

Driver contract uses internal scheduler IDs and never receives Candidate
tokens directly. A future DPDispatcherDriver is optional and must pass the
same conformance suite before use; it does not affect Case or Agent interfaces.

## Trusted SiteProfile

The evaluator-private profile owns:

- SSH target/user/credential provider;
- remote root;
- scheduler/account/partition/QOS;
- Apptainer path and frozen runtime store;
- module/source policy;
- resource ceilings;
- GPU request mapping and qualification;
- transfer policy;
- infrastructure retry policy;
- cleanup/settlement policy.

Candidate, Case, Skill, request, public RunRecord, and public evidence never
contain credential values or raw connection details. RunRecord stores a
secret-free SiteProfile digest and qualification receipt.

For the site, the trusted mapping retains:

```text
account = acct-blocked
partition = cpu/gpu
qos = normal/long
GPU request = --gres=gpu:1
GPU verification = AllocTRES plus trusted device probe
```

## Trusted Runtime Wrapper

Agent commands never run in the host HPC login environment. The driver emits a
trusted scheduler script whose scientific command is wrapped as:

```bash
apptainer exec \
  --containall \
  --cleanenv \
  --no-home \
  --bind "$RUN_DIR:/workspace:rw" \
  "$PINNED_SIF" \
  <validated argv>
```

The Agent cannot alter wrapper flags, bind host paths, select runtime paths,
inject scheduler directives, or choose site modules. Formal qualification is
behavioral: hidden/other-run/HOME/credential paths must be unreadable inside
the scientific runtime; flags alone are not accepted as proof.

## Retry and Recovery

Dispatcher may retry only transient infrastructure operations under a frozen
budget: SSH connect/query and file-transfer transport. Scientific process
failure, OOM, timeout, parser failure, or bad input returns to the Agent.

Before calling `sbatch`, Dispatcher durably appends and fsyncs a submission
intent keyed by `(run_id, operation_id, attempt)` and an opaque scheduler
marker. After `sbatch`, it records the returned scheduler ID and receipt. If a
crash occurs between those writes, recovery searches only for that exact
marker, adopts at most one matching job, and fails closed on zero-or-multiple
ambiguous matches. It never resubmits merely because the local receipt is
missing.

The Agent inspects logs, modifies inputs/resources within limits, and submits a
new `attempt`. The Coordinator owns waiting and resume; queue wait consumes
total walltime but not model turns or Agent active time.

The first v2 HPC Pilot freezes `agent_turn_limit = 128` for both NS and WS.
This is an active-reasoning ceiling, not a scheduler polling budget; model/API
identity and all timeout/budget values are Harness-owned and identical across
the pair. Cases and Skills cannot configure providers, credentials, models or
turn limits.

## Fetch and Evidence

Remote success does not create a Candidate submission. Only explicit
`bench-hpc fetch` copies declared outputs into Candidate space. Trusted Infra
may seal remote diagnostic evidence, but the Verifier cannot use it to repair
missing submission artifacts.

Run evidence records:

- validated request and digest;
- operation/attempt/scheduler job mapping;
- generated trusted scheduler script digest;
- forward/backward manifests;
- state history, exit code, queue/run time;
- ReqTRES/AllocTRES and resource usage;
- logs and returned artifact hashes;
- settlement/cancel/cleanup result;
- driver, site, runtime and tool identities.

Before output retrieval, Dispatcher performs trusted `lstat`/realpath
validation. Symlinks, hardlinks, devices, FIFOs, sockets and nodes escaping the
run root are rejected; transfer never follows a remote symlink into personal
HOME or another run. A declared archive is transferred only as an opaque
regular file with size/hash evidence; Dispatcher never extracts it.

The local fetch destination is also untrusted. Dispatcher rejects destination
symlinks and escapes, writes into a fresh same-filesystem staging directory,
fsyncs bytes and manifest, and atomically publishes according to an explicit
no-overwrite policy. A partial transfer can never replace an existing
Candidate artifact.

Settlement acts only on exact scheduler IDs stored in the run ledger. It never
cancels by username, name prefix or directory scan, which could affect
unrelated jobs sharing the evaluator account.

## Public Distribution

Other evaluators reuse Case, Skill, CLI, Dispatcher, Verifier, and schemas.
They supply a private SiteProfile and credential provider, then pass driver
conformance plus a small site canary. Formal v1 supports Slurm only; other
schedulers are future drivers.

Cross-site results retain site/hardware/runtime identities and are not treated
as byte-identical environments. Scientific outcome is primary; queue wait is
reported separately.

## Migration Strategy

1. Establish a clean Infra v2 baseline and accurate R3 review.
2. Add HpcDispatcher façade with zero behavior change.
3. Version ExecutionRequest while preserving JobSpec compatibility.
4. Route all production HPC calls through HpcDispatcher.
5. Freeze and qualify the the site SiteProfile and runtime wrapper.
6. Run ProcessDriver conformance and authorized real canaries.
7. Run one Pilot per HPC lineage through the sole Dispatcher path.
8. Freeze release identities and activation evidence.
9. Only then remove MatClaw/case-policy/legacy compatibility code.

Removing infrastructure hints from an existing Case instruction is a
versioned Case/release change, not part of the zero-behavior façade refactor.
The next protocol supplies tool availability uniformly through Harness while
Cases remain scientific rather than prescribing a backend.

## Formal Gates

- D0: clean baseline; full suite has only explicitly owned release REDs;
- D1: façade parity for all Agent-facing operations;
- D2: request validation/path/resource/operation-attempt contract;
- D3: secret-free SiteProfile and credential isolation;
- D4: trusted runtime containment behavior;
- D5: durable submission intent, exact-marker recovery and crash/resume without
  duplicate scheduler work;
- D6: real scheduler state/log/fetch/cancel/usage;
- D7: GPU/TRES/runtime qualification;
- D8: explicit fetch and artifact integrity;
- D9: settlement removes/reconciles orphan jobs;
- D10: five HPC lineages have zero Case-ID dispatcher branches;
- D11: RunRecord/evidence/release digests recompute;
- D12: no Formal path uses ProcessDriver/fake scheduler.

## Non-Goals

- Candidate raw SSH or personal HOME access;
- adding DPDispatcher as a main dependency;
- portable mini-HPC as a Formal backend;
- physical file/module consolidation before behavioral parity;
- supporting PBS/LSF in the first release;
- changing scientific thresholds to accommodate Infra changes.
