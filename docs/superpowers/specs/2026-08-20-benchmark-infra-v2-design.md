# Benchmark Infra v2: Durable, Centralized, Case-Agnostic Design

**Status:** Approved design baseline (2026-08-20)  
**Scope:** Cases 001–042: 37 `local_sandbox` cases and five target
`hpc_controller` cases (031–034 and 042).  
**Supersedes at implementation time:** the case-number-specific runtime and
HPC migration portions of `2026-08-17-mlip-benchmark-infrastructure-design.md`.
The existing trust boundary, evidence, and scientific result principles remain
normative unless this document explicitly strengthens them.

## 1. Goal

Build one benchmark infrastructure in which every Case declares only its
scientific task, public inputs, output contract, execution class, capability
requirements, and verifier. The trusted Infra exclusively owns:

- model/provider selection;
- API endpoint, retry, timeout, and secret handling;
- Agent turn, time, and tool policy;
- No-Skill/With-Skill treatment;
- Candidate, control, compute, and Verifier runtime resolution;
- durable session/checkpoint recovery;
- HPC capability issuance and settlement;
- submission collection and sealing;
- immutable resolved identity and result classification.

The immediate outcome is an infrastructure that can run long scientific Agent
tasks without conflating API failures, scheduler waits, filesystem defects, or
configuration drift with scientific performance.

## 2. First-Principles Invariants

These invariants are the source of all later design decisions.

### P1. A benchmark freezes the strongest identity it can prove

Every counted observation is produced from one immutable resolved benchmark
configuration. Human-friendly aliases such as `formal-long` or
`benchmark-default` are inputs to a resolver, never sufficient provenance. The
run stores their resolved values and SHA-256 digests before Candidate startup.

For self-hosted models, the model weights and serving runtime digests are
required. For remote APIs, Infra freezes the provider, model/deployment/version
identity, request policy, and strongest response metadata the provider exposes.
If the provider exposes no immutable version, the lock records
`identity_strength = "alias-only"` and the benchmark does not claim byte-level
model immutability.

### P2. A Case describes science, not the evaluator's Agent service

A Case may not contain provider, model, endpoint, API key, retry policy,
request timeout, turn limit, Agent walltime, skill treatment, or endpoint
fallback. Schema validation rejects these fields.

### P3. Untrusted work never owns secrets or trusted control

The model provider client and API secrets live in the trusted Harness process.
They are never injected into the Candidate sandbox. HPC credentials live only
in the trusted Gateway/Adapter. The Candidate receives a constrained tool
surface and, for HPC, one run-scoped capability token.

The Candidate never initiates a model-provider request. The trusted Harness
mediates all provider I/O, admits one model response into the durable session,
executes the response's tool requests in the Candidate, and returns only tool
results to the next trusted model request.

### P4. Long duration is not equivalent to unlimited model calls

Model reasoning, local tools, scheduler waiting, remote compute, and API retry
are distinct activities with distinct budgets. Scheduler waiting and API retry
do not consume model turns. Formal runs always have finite, frozen budgets.

### P5. Every external effect has durable logical identity

Model requests, job submissions, artifact fetches, submission sealing, and
record creation have stable logical operation IDs. Where an external system
supports idempotency, Infra uses it. Otherwise recovery guarantees at most one
accepted benchmark transition while explicitly recording every external
attempt, including attempts whose remote completion is unknown. The design
does not claim physical exactly-once execution from an ordinary remote LLM API.

### P6. Failure attribution precedes scoring

Scientific PASS/FAIL exists only after the submission satisfies its declared
structural contract and the independent Verifier completes successfully.
Missing or malformed structurally required artifacts are
`AGENT_FAILURE/INVALID_SUBMISSION`; scientific content or metrics below the
Verifier threshold are `VALID_RESULT/SCIENTIFIC_FAIL`. API, Harness, sandbox,
Gateway, scheduler, image, and Verifier-runtime failures are `INFRA_INVALID`.
A frozen Agent budget exhausted after otherwise healthy Infra is
`AGENT_FAILURE`. None is collapsed into an unclassified reward of zero.

### P7. Common Core dispatches by contract, never by Case ID

Neither Common Core nor a shared plugin may dispatch on Case identity. No
runtime branch may mention `031`, `032`, `033`, `034`, or `042`. Dispatch is
driven only by `execution.class`, category, declared capabilities, runtime
requirements, Verifier contract type, and site-adapter type. Case-specific
science remains in Case data/config/reference/verifier parameters. New cases do
not require Common Core or shared-plugin edits.

### P8. Evidence bytes and state transitions are append-only

Candidate bundle digests, resolved locks, Agent events, Gateway events,
submission seals, Verifier results, and RunRecords are immutable. Recovery
adds events; it does not rewrite prior history.

Every event carries a monotonic `event_seq`, stable `event_id`, logical
`operation_id`, `previous_event_digest`, and `event_digest`. This linear hash
chain makes deletion, insertion, reordering, and modification mechanically
detectable without requiring a Merkle-tree service.

### P9. A counted comparison varies only the declared treatment

For paired or comparative results, every resolved-lock field outside the
experiment's declared treatment must be identical. A mechanical lock diff
returns allowed and unexpected differences; any unexpected difference makes
the comparison invalid rather than merely warning.

## 3. System Boundaries

```text
Central Config + Secret Provider
              |
              v
      AgentConfigResolver  ---> resolved-run-lock.json (immutable)
              |
              v
      Durable Run Coordinator <---- append-only session/checkpoints
       |          |          |
       |          |          +---- Model/API client (trusted, secrets here)
       |          |
       |          +--------------- Candidate Sandbox (untrusted hands)
       |                              |
       |                 execution.class == hpc_controller
       |                              |
       |                       bench-hpc capability
       |                              |
       |                    Trusted Gateway + Site Adapter
       |                              |
       |                      frozen Compute Runtime
       |
       +---- freeze -> collect -> quarantine -> seal
                                      |
                               fresh Verifier Runtime
                                      |
                          Result + immutable RunRecord
```

The three durable interfaces are:

1. **Session:** append-only Agent and activity events plus checkpoint digests;
2. **Harness:** resolves identity and advances the workflow;
3. **Execution:** replaceable Candidate sandboxes and HPC site adapters.

## 4. Configuration Ownership

| Concern | Case | Central Infra | Experiment | Secret Provider | RunRecord |
|---|---:|---:|---:|---:|---:|
| Scientific instruction/public inputs | owns | no | no | no | digest |
| `execution.class` and capabilities | owns | validates | no | no | resolved |
| Scientific runtime requirements | owns implementation/version constraints required by science | resolves concrete runtime profile/image | freezes resolved profile | no | requirements+resolved digest |
| Verifier contract | owns | launches | freezes digest | no | digest |
| Provider/model identity | forbidden | registry | selects alias | no | resolved+digest |
| API endpoint/retry/timeout | forbidden | registry | selects alias | credential only | secret-free resolved+digest |
| Turn/time/tool budget | forbidden | defaults | owns | no | resolved+usage |
| Skill treatment | forbidden | resolves bundle | owns | no | resolved+digest |
| Site/queue/credentials | forbidden | site registry | selects eligible site | credential only | secret-free digest |

### 4.1 Central registry

The target tree is:

```text
infra/
  config/
    agent-profiles.toml
    model-profiles.toml
    api-profiles.toml
    experiment-profiles.toml
    runtime-profiles.toml
    site-profiles.toml
  schemas/
    agent-profile.schema.json
    resolved-run-lock.schema.json
  secrets/
    README.md                 # values never committed
```

### 4.2 Resolution and freeze phases

Before an experiment is frozen, construction resolves:

```text
explicit CLI profile selection
  > experiment template
  > central profiles
  > Infra defaults
  -> resolved experiment manifest
  -> freeze
```

After freeze, Formal execution has no field-precedence chain:

```text
frozen experiment manifest
  + selected case_id/run_id/replicate
  -> resolved-run-lock
```

Formal CLI accepts only the frozen `experiment_id`, `case_id`, `run_id`, and
replicate selection. Any model, endpoint, retry, timeout, budget, skill,
runtime, site, concurrency, tool, prompt, or sampling override is rejected as
`FROZEN_EXPERIMENT_OVERRIDE_REJECTED`. Case manifests never have
Agent/API/budget override authority.

### 4.3 Resolved lock

Before any sandbox or Gateway starts, the resolver writes an immutable lock
containing at least:

```json
{
  "schema": "resolved-run-lock/v2",
  "run_id": "...",
  "case": {
    "case_id": "...",
    "case_digest": "sha256:...",
    "release_digest": "sha256:...",
    "candidate_bundle_digest": "sha256:..."
  },
  "experiment": {
    "experiment_id": "...",
    "manifest_digest": "sha256:...",
    "declared_treatment": "skill_availability",
    "condition_id": "no-skill",
    "budget_policy_digest": "sha256:...",
    "max_concurrent_runs": 1
  },
  "agent": {
    "agent_profile": "formal-long",
    "agent_profile_digest": "sha256:...",
    "provider": "...",
    "model_id": "...",
    "deployment_id": "...",
    "provider_model_version": "...",
    "identity_strength": "provider-versioned",
    "model_identity_digest": "sha256:...",
    "agent_engine_digest": "sha256:...",
    "system_prompt_digest": "sha256:...",
    "tool_schema_digest": "sha256:...",
    "tool_registry_digest": "sha256:...",
    "tool_implementation_digest": "sha256:...",
    "tool_help_digest": "sha256:...",
    "sampling_policy_digest": "sha256:...",
    "context_policy_digest": "sha256:...",
    "skill_bundle_digest": null
  },
  "api": {
    "api_profile": "primary-proxy",
    "api_profile_digest": "sha256:...",
    "endpoint_identity_digest": "sha256:...",
    "retry_policy_digest": "sha256:...",
    "timeout_policy_digest": "sha256:...",
    "credential_profile_id": "primary-proxy"
  },
  "candidate_runtime": {
    "runtime_requirements_digest": "sha256:...",
    "runtime_profile": "...",
    "runtime_profile_digest": "sha256:...",
    "candidate_image_digest": "sha256:...",
    "control_image_digest": "sha256:..."
  },
  "hpc": {
    "enabled": true,
    "site_profile_digest": "sha256:...",
    "gateway_digest": "sha256:...",
    "adapter_digest": "sha256:...",
    "compute_runtime_digest": "sha256:..."
  },
  "verifier": {
    "contract_digest": "sha256:...",
    "code_digest": "sha256:...",
    "runtime_digest": "sha256:..."
  },
  "infra": {
    "harness_commit": "...",
    "eval_sha256": "sha256:...",
    "container_runtime_identity": "...",
    "protocol_version": "..."
  },
  "budgets": {
    "max_model_turns": 512,
    "max_logical_model_requests": 512,
    "max_api_attempts": 2048,
    "max_total_tokens": 100000000,
    "max_model_cost_usd_micros": 100000000,
    "agent_active_walltime_sec": 86400,
    "run_total_walltime_sec": 691200,
    "local_tool_walltime_sec": 43200,
    "api_retry_walltime_sec": 7200,
    "scheduler_wait_walltime_sec": 604800,
    "remote_compute_quota_digest": "sha256:..."
  }
}
```

The credential profile ID may be placed in a restricted RunRecord extension
when site policy considers it sensitive. Secret values never appear in the
lock, session, evidence bundle, or public RunRecord.

## 5. Long-Flow Agent Policy

### 5.1 Turn definition

One turn is one successful model response admitted into the Agent session,
including any tool calls returned by that response.

The following do not consume turns:

- failed API attempts that produce no model response;
- local tool runtime;
- HPC queue or execution waiting;
- artifact transfer;
- Harness restart and checkpoint restoration;
- independent verification.

All still consume and record their own wall-clock and attempt budgets.

Every Formal budget domain is finite and independently enforced:

- logical model requests and accepted model turns;
- API attempts and cumulative API retry walltime;
- provider-reported tokens and resolved maximum model cost;
- Agent active walltime and total run walltime;
- local tool walltime;
- scheduler wait walltime;
- remote job count, CPU-hours, GPU-hours, storage, and per-job walltime.

The frozen cost ceiling is numeric in USD micro-units. At experiment
construction it is computed from the profile's token ceiling and a frozen
provider rate-card snapshot, rounded upward; a price change after freeze does
not silently alter the experiment. A failed attempt consumes API-attempt and
retry-walltime budget and consumes token/cost budget whenever the provider
reports billable usage, even though it consumes no accepted turn.

### 5.2 Standard profiles

| Profile | Intended use | Max model turns | Max total tokens | Agent active walltime | External wait deadline |
|---|---|---:|---:|---:|---:|
| `pilot-infra` | machinery validation | 128 | 25 M | 2 h | 48 h |
| `discovery-long` | case diagnosis/042 Discovery | 256 | 50 M | 12 h | 7 d total |
| `formal-long` | 031–034/042 long workflows | 512 | 100 M | 24 h | 7 d total |
| `local-standard` | 001–030/035–041 | 64 | 10 M | 2 h | 0 h |

Formal experiments may define another finite value only by creating a new
experiment version before observations exist. `max_model_turns = unlimited`
is rejected for Pilot and Formal.

The external-wait value is a total run budget. Each HPC job also has its own
resource-profile walltime ceiling. Queue time and job runtime are recorded
separately so the evaluator can distinguish site contention from scientific
compute.

Agent active walltime includes model request time and local tool execution but
excludes Coordinator-owned external waiting. Token accounting uses provider
reported input, cached, reasoning, and output tokens. A provider that cannot
produce usage records is ineligible for Formal until an equivalent auditable
counter is qualified.

The profile table supplies common Agent ceilings. Each frozen experiment also
resolves finite total-run, API-attempt/retry, local-tool, and remote-compute
quotas appropriate to its suite. Remote quotas are derived from Case runtime
requirements plus the selected resource profile; a Case never selects a
concrete queue or image.

Before Formal activation, a Pilot must show that the turn ceiling is not the
dominant failure mode. If more than 10% of otherwise healthy Pilot runs exhaust
the ceiling, the experiment profile is recalibrated and versioned before any
Formal observation; the limit is never raised after Formal results are seen.

### 5.3 Durable checkpoints

A checkpoint is written after:

- every accepted model response;
- every tool result;
- every remote job submission and terminal transition;
- every artifact fetch;
- Candidate freeze;
- submission seal;
- Verifier completion.

Each logical model operation has one operation ID and an append-only attempt
list. An attempt may end as `success`, `known_failure`, or `timeout_unknown`.
When a provider lacks idempotency support, recovery may issue another physical
request, but exactly one complete response is admitted to the benchmark
session as `accepted_attempt`. Partial streamed responses and late responses
from non-accepted attempts are retained as restricted diagnostics and never
become Agent input.

It contains the session cursor, current lifecycle phase, cumulative budget,
outstanding activity IDs, owned job IDs, workspace/seal digests, and resolved
lock digest. It contains no secret values.

The complete append-only session remains authoritative. A compacted or
model-generated handoff summary is only an acceleration artifact and carries
its own digest. If generating that summary requires a model response, that
response consumes the same frozen model/token budget. Context compaction and
truncation policy are part of `context_policy_digest` and pair comparability.

Resume requires the same resolved lock digest. A changed model, endpoint,
budget, skill, case, runtime, or site digest creates a new run rather than
resuming the old one.

### 5.4 External waiting

After `bench-hpc submit`, the Agent may yield control. The Coordinator records
`WAITING_EXTERNAL`, polls through the trusted Gateway without an LLM call, and
resumes the Agent only when:

- a job reaches a terminal state;
- a configured progress event occurs;
- the external deadline expires;
- cancellation is requested.

Repeated `sleep` tool calls and model-driven scheduler polling are prohibited
in Formal profiles. This prevents queue latency from measuring Agent verbosity.

## 6. Model/API Transport Policy

### 6.1 Separation

- **Model identity:** provider and immutable model identifier/version;
- **API transport:** endpoint, connection/request timeout, retry policy;
- **Experiment policy:** turns, active time, external wait, tools, skills;
- **Secrets:** credential lookup available only to the trusted provider client.

No automatic cross-model or cross-endpoint fallback is permitted in Formal
runs. A transport failure exhausts its frozen retry policy and becomes
`INFRA_INVALID`. Pilot experiments may exercise failover only under a distinct
experiment ID, and those observations never enter Formal results.

### 6.2 Default request policy

```yaml
connect_timeout_sec: 15
request_timeout_sec: 900
max_attempts: 4
backoff: full_jitter
base_delay_sec: 1
max_delay_sec: 30
honor_retry_after: true
```

Retryable conditions:

- connection reset, DNS/transient socket failure, read timeout;
- HTTP 408, 429, 500, 502, 503, 504, 529.

Non-retryable conditions:

- HTTP 400 invalid request;
- HTTP 401/403 authentication or authorization;
- HTTP 402 quota/balance;
- unknown model or unsupported endpoint feature.

Retries use the provider request ID/idempotency mechanism when available.
One retry budget exists at the provider boundary; nested SDK+Harness retry
loops are forbidden.

The provider layer also owns a bounded circuit breaker. Repeated endpoint-wide
failures stop admission of new runs instead of generating a retry storm.
Already-started Formal runs are classified by their frozen policy; they are not
moved to another endpoint.

### 6.3 API failure taxonomy

| Condition | Result | Failure code | Retryable run |
|---|---|---|---:|
| 504/timeout exhausted | `INFRA_INVALID` | `API_TRANSIENT_EXHAUSTED` | yes |
| 429 exhausted | `INFRA_INVALID` | `API_RATE_LIMIT_EXHAUSTED` | yes |
| 401/403 | `INFRA_INVALID` | `API_AUTH_CONFIG` | after config change |
| 402 balance/quota | `INFRA_INVALID` | `API_QUOTA` | after quota change |
| invalid model/request | `INFRA_INVALID` | `API_CONFIGURATION` | after config change |
| valid responses reach turn limit | `AGENT_FAILURE` | `AGENT_BUDGET_EXHAUSTED` | no silent retry |

Raw HTML error bodies are stored only in restricted diagnostics; public
RunRecords store normalized status, provider request ID, attempt count, and a
redacted reason.

## 7. Case-Agnostic Execution for 001–042

### 7.1 Dispatch

The sole top-level dispatch is:

```python
executor = executor_registry.resolve(case.execution.class)
```

`local_sandbox` and `hpc_controller` implement one stable interface. Category
and site variations are plugins resolved from capabilities, not `if case_id`.

### 7.2 Runtime identities

Four logical identities are always recorded even if an implementation combines
two into one OCI image or two identities resolve to the same digest:

1. **Candidate image:** untrusted local scientific workspace;
2. **Control image:** minimal `bench-hpc` client for HPC cases;
3. **Compute runtime:** frozen remote scientific OCI/SIF runtime;
4. **Verifier image:** independent, networkless grading runtime.

For `local_sandbox`, Candidate is decision plus compute and `control_image` is
null. For `hpc_controller`, the sandbox runs the resolved control image plus
the allowlisted Case bundle; `candidate_image_digest` records that final
sandbox identity while `control_image_digest` records the reusable control
runtime lineage. Scientific compute occurs only in the remote compute runtime.

### 7.3 Local cases 001–030 and 035–041

All 37 Local cases must:

- explicitly declare `execution.class = local_sandbox`;
- pass the same strict `CaseSpec` loader used by the runtime;
- retain legacy `submission_root = "."` only where scientifically required;
- resolve root or legacy Dockerfiles through one build/runtime resolver;
- pass a gold-solution Candidate-to-Verifier smoke;
- have every Infra-resolved runtime image digest locally resolvable or fail
  preflight before an Agent/API call.

This includes restoring/building `dftworld-base-packmol` for Case 006 and
supporting root Dockerfiles in `base-env-build/build.sh`.

### 7.4 HPC cases 031–034 and 042

The Common Core sees five identical `hpc_controller` contracts. Plugins own
only scientific/runtime capability translation:

| Cases | Category plugin | Required remote capabilities |
|---|---|---|
| 031–033 | `mlp.matclaw-cips` | Slurm, GPU DeePMD/LAMMPS, artifact fetch |
| 034 | `mlp.ai2kit-water` | CP2K, DeePMD/ai2-kit, CPU+GPU stages |
| 042 | `mlp.deepmd-jax-go-water` | CP2K, DPMP/JAX, CPU labeling, optional GPU training |

Case 042 remains a Discovery Case until G5–G12 close. It uses the same Infra
but is excluded from Formal ablation and the current frozen release.

## 8. Gateway Security and HPC Durability

The generic Gateway, not a Candidate-side CLI, enforces every boundary.

### 8.1 Capability and ownership

- token is bound to `run_id`, case, site, allowed operations, quota, and expiry;
- every submitted `job_id` is inserted into a Gateway-owned ledger;
- status/log/fetch/cancel reject jobs not owned by the token's run;
- token revocation disables all later operations;
- settlement waits, cancels, or classifies every owned job before teardown.

### 8.2 Path containment

- local inputs must resolve beneath the run's Candidate workspace;
- local fetch destinations must resolve beneath the same workspace;
- remote paths are generated by the Gateway under
  `<site-root>/<case-id>/<run-id>/<job-id>/`;
- absolute Candidate-supplied host paths, `..`, symlinks, and cross-run remote
  paths are rejected after canonical resolution;
- Gateway adapters never accept an arbitrary shell command; job commands are
  argv arrays validated against the job schema.

### 8.3 Idempotency and audit

`submit` requires an idempotency key derived from run ID plus logical activity
ID. The adapter writes that operation ID into scheduler job metadata and a
remote operation marker, then queries it during recovery. If the operation
already owns a job, the Gateway reuses its job ID; otherwise it submits once.
Duplicate submission returns the original job. Every operation appends a
secret-free audit event with request ID, run ID, job ID, resource request,
state, artifact hashes, and timestamp. Logical idempotency is mandatory;
physical exactly-once is recorded as a backend capability, not assumed.

### 8.4 CLI

Every control image exposes one `bench-hpc` CLI:

```text
bench-hpc help
bench-hpc capabilities
bench-hpc submit job.yaml
bench-hpc status JOB_ID
bench-hpc logs JOB_ID
bench-hpc fetch JOB_ID OUTPUT_DIR
bench-hpc cancel JOB_ID
bench-hpc usage
```

Unknown operations return exit 2 plus the legal operation list. CLI startup is
part of image qualification, including all Python dependencies.

## 9. Submission, Quarantine, and Verifier

### 9.1 Collection

The collector uses `lstat` before copying. It rejects symlink files and
directories, FIFOs, sockets, devices, hardlinks, and setuid/setgid nodes at the
source boundary. It never follows a Candidate symlink. A broken symlink and a
valid symlink produce the same deterministic `INVALID_SUBMISSION` result.

Framework-generated aliases such as TensorFlow checkpoint symlinks must be
materialized into regular files or excluded from the declared final submission
before freeze. The Infra provides a deterministic submission-normalization
helper, but it never follows a symlink on behalf of an untrusted Candidate.

Collection and quarantine share one filesystem-node policy so a node cannot be
transformed into a safer-looking type between boundaries.

Before scientific verification, the trusted Harness validates the Case's
declarative submission contract: required relative paths, allowed file types,
size bounds, and structural schemas. It does not execute Candidate code or
judge scientific values. The classification pipeline is:

```text
source-safe collection
  -> structural submission validation
       -> invalid: AGENT_FAILURE / INVALID_SUBMISSION
       -> valid: quarantine and seal
          -> Verifier runtime startup
               -> runtime failure: INFRA_INVALID / VERIFIER_FAILURE
               -> healthy scientific evaluation
                    -> VALID_RESULT / PASS or SCIENTIFIC_FAIL
```

Only requirements explicitly declared in the Case submission contract can
trigger structural `INVALID_SUBMISSION`. Scientific completeness, numerical
quality, convergence, and thresholds remain the independent Verifier's domain.

### 9.2 Verifier runtime

The Verifier remains fresh, networkless, read-only, and starts only after
Candidate destruction. The current root-only Python conflict is solved in the
image/runtime contract rather than by permanently weakening isolation:

- qualified Verifier Python and dependencies must be readable/executable by a
  fixed non-root UID;
- `--user 65532:65532` is restored after all Verifier images qualify;
- Verifier dependencies live under a runtime path such as
  `/opt/dftworld/venv`, never `/app/.venv`, because the sealed submission mount
  intentionally hides the image's `/app` tree;
- legacy tests that invoke `/app/.venv/bin/python` are migrated to the
  qualified Verifier runtime and receive a regression test proving they never
  execute a Candidate-supplied interpreter;
- no Verifier executes Candidate-provided code outside the sealed submission
  data contract;
- `/solution` and `/reference` remain Verifier-only read-only mounts.

Tmpfs sizing is resolved from a Verifier profile with a bounded default and is
recorded, rather than hard-coded globally.

### 9.3 Historical three-layer defects

Regression fixtures permanently cover:

**Layer A:** absolute volumes, non-root readable Python, terminal names,
Verifier-only `/solution`, sufficient tmpfs, and 031's direct `verify()` entry.

**Layer B:** 031 checkpoint symlinks, 032 scheduler waits, 033 504 separation
from its scientific slope, API timeout/rate/quota/auth errors, and remote
artifact restoration.

**Layer C:** runtime overlay qualification, CLI discovery, execution-class
dispatch, and control/compute/Verifier image separation across all 001–042.

## 10. Durable Lifecycle and RunRecord

### 10.1 Lifecycle

The canonical top-level lifecycle keeps monotonic phases. Repeatable work is
recorded as activity events inside `CANDIDATE_RUNNING`, not as backward phase
transitions.

```text
CREATED -> CONFIG_RESOLVED -> PACKAGED -> CANDIDATE_STARTING
        -> CANDIDATE_RUNNING -> CANDIDATE_STOPPING -> CANDIDATE_FROZEN
        -> SUBMISSION_COLLECTED -> CANDIDATE_DESTROYED
        -> QUARANTINED -> SEALED -> VERIFYING -> COMPLETED
```

Activity events include model attempts, accepted turns, tool calls,
checkpoints, HPC submissions, waits, resumes, fetches, and settlement.

Serialized events use:

```json
{
  "event_seq": 138,
  "event_id": "evt_...",
  "operation_id": "MODEL-000124",
  "previous_event_digest": "sha256:...",
  "event_digest": "sha256:...",
  "timestamp": "...",
  "payload": {}
}
```

The event digest covers the canonical event body and previous digest.
Validation recomputes the complete chain before resume and before RunRecord
finalization.

Exactly one terminal phase is appended: `COMPLETED`, `FAILED_AGENT`, or
`INVALID_INFRA`. `record_write` is an action, not a lifecycle phase.

### 10.2 RunRecord v2

RunRecord v2 adds:

- resolved lock and component digests;
- max turns, accepted turns, API attempts, token usage, and active/wait time;
- `model_attempts[]` with logical operation ID, physical attempt number,
  terminal attempt state, provider request ID, response-metadata digest,
  billable usage, and the sole accepted-attempt marker;
- checkpoint and session-log digests;
- Candidate/control/compute/Verifier image digests;
- real site name and site-config digest;
- `remote_jobs[]` with Gateway job IDs, states, resources, and artifact hashes;
- one validated terminal lifecycle;
- normalized API/HPC/Agent failure codes.

RunRecord validation replays lifecycle legality and confirms that its result
class agrees with the terminal phase.

### 10.3 Invalid-run ledger

Every Formal `INFRA_INVALID` RunRecord is atomically and automatically added to
the experiment's append-only invalid-run ledger. Manual editing is forbidden.
The formal aggregator refuses an unledgered invalid run or a ledger entry whose
RunRecord is missing.

## 11. Release and Pair Comparability

Before a Pilot or Formal run starts, preflight recomputes every release digest
from disk. Any mismatched frozen component fails before an API call. Unrelated
untracked or dirty user files outside the release manifest do not block a run.
The runner may not merely write the old source commit into a new RunRecord.

No-Skill and With-Skill comparability requires equality of:

- case/release digests;
- provider/model/API profile and resolved digests;
- turn, request, token, cost, active-time, total-time, external-wait,
  request-retry, local-tool, and remote-compute policies;
- system prompt, tool schema, tool registry/implementation, tool help surface,
  sampling, and context policies;
- Candidate/control/compute/Verifier images;
- site/profile/resource enforcement;
- sampling and provider settings;
- replicate identity.

The only permitted difference is the frozen skill treatment and its physical
bundle availability.

`compare_lock(left, right, declared_treatment)` returns
`allowed_differences` and `unexpected_differences`. A counted comparison
requires the latter to be empty. A model-comparison experiment may declare
model identity as its treatment; a skill-ablation experiment may not.

Pair scheduling is also frozen. No-Skill and With-Skill arms are randomized or
alternated within the same declared time block, use identical concurrency, and
record provider latency plus scheduler queue time. The aggregator reports
infrastructure-invalid rate and latency distributions alongside scientific
scores; it never hides time-of-day or resource-contention imbalance.

## 12. Validation Strategy

### 12.1 Unit and schema tests

- Case rejects Agent/API/secret/budget fields;
- resolver precedence and canonical digest are deterministic;
- frozen Formal CLI field overrides are rejected;
- remote-model identity strength is explicit and never overclaimed;
- RunLock contains no secret values;
- budget accounting excludes API retry and external wait from turns;
- logical model attempts admit at most one response while retaining unknown
  and duplicate physical attempts;
- event sequence/hash-chain deletion, insertion, reorder, and mutation fail;
- lifecycle has exactly one legal terminal state;
- structural submission failure is distinct from scientific failure and
  Verifier-runtime failure;
- RunRecord lock comparison covers treatment, tools, runtimes, HPC, Verifier,
  experiment, Agent, API, and all budget fields.

### 12.2 Fault injection

- 408/429/500/502/503/504/529 with jittered bounded retry;
- 400/401/402/403 fail fast;
- process kill after model response, tool result, submit, fetch, seal, and
  Verifier result, followed by exact resume;
- duplicate submit with one scheduler job;
- LLM response lost after provider execution, followed by one accepted retry;
- Gateway token/job/path/cross-run attacks;
- valid and broken symlink collection attacks;
- missing/corrupt image and release digest;
- scheduler queue longer than Agent active time.

Repository test discovery is itself an Infra gate: the documented root test
command must not collect case-private/evidence/job copies as duplicate Python
modules. `testpaths`/ignore rules make the canonical suite deterministic.

### 12.3 End-to-end matrix

1. Local gold smoke for all 37 Local cases;
2. fake-adapter HPC conformance for 031–034 and 042;
3. real-site one-job canary for each runtime plugin;
4. real No-Skill/With-Skill Pilot pair using identical resolved locks except
   skill treatment;
5. fresh-process restore and Verifier replay from durable evidence.

## 13. Activation Roadmap

Activation is fail-closed and proceeds in this order:

1. **Freeze current experiments:** no new Formal arms; preserve ongoing bytes
   as diagnostic-only evidence.
2. **Close P0 trust defects:** Gateway containment/ownership and source-side
   symlink rejection.
3. **Centralize Agent/API/Experiment config:** schema rejection in Cases,
   resolver, secret provider, resolved lock.
4. **Add Durable Coordinator:** checkpoints, activity IDs, recovery, external
   wait, budget accounting.
5. **Upgrade API transport:** taxonomy, bounded retry, jitter, redaction,
   fault injection.
6. **Generalize execution:** registry by execution class and capabilities;
   remove all Case-ID branches.
7. **Migrate 001–042:** 37 Local strict contracts plus five HPC plugins;
   qualify every declared runtime.
8. **Restore Verifier isolation:** non-root qualified images, source-safe
   collector, monotonic lifecycle, RunRecord v2.
9. **Re-freeze releases/protocols:** recompute all digests; add Agent/API/budget
   identity; keep 042 outside Formal until G5–G12 close.
10. **Activation tests:** Local gold matrix, fake HPC matrix, real-site
    canaries, then one Pilot pair per HPC case.
11. **Formal enablement:** only after every hard gate below passes.

## 14. Hard Gates

### INFRA-G-AGENT-CONFIG

- centralized model, API, experiment, runtime, and secret profiles;
- Case schema mechanically forbids Agent/API/budget/secret fields;
- one resolver and immutable resolved lock;
- RunRecord stores secret-free resolved identities and digests;
- future profile edits cannot alter old runs.

### INFRA-G-COMPARISON

- every experiment declares its sole treatment dimension;
- mechanical lock comparison permits only that dimension;
- tool schema/implementation/help, prompt, context, sampling, budget,
  concurrency, runtime, site, and Verifier identities are compared;
- any unexpected lock difference makes the comparison invalid.

### INFRA-G-DURABILITY

- checkpoint after every external side effect;
- process-kill recovery produces no duplicate model admission and reuses HPC
  jobs through logical operation identity;
- all physical model attempts, including `timeout_unknown`, remain auditable;
- event sequence and hash chain validate before resume/finalize;
- scheduler waiting consumes no model turn;
- resume requires identical resolved lock;
- finite Pilot/Formal budget.

### INFRA-G-API

- retry taxonomy, idempotency, full jitter, retry cap, redaction;
- no Formal cross-model fallback;
- API failures normalize to machine-readable Infra codes;
- injected 504 never becomes scientific failure.

### INFRA-G-EXECUTION

- no Case identity dispatch in Common Core or shared plugins;
- all 001–042 pass strict CaseSpec;
- distinct Candidate/control/compute/Verifier identities;
- all declared images and CLI surfaces qualify before Agent startup.

### INFRA-G-TRUST

- Gateway path containment, run/job ownership, quotas, settlement, revocation;
- Candidate receives no API/HPC credential except scoped capability token;
- collector never follows symlinks;
- Candidate destroyed before independent Verifier starts;
- Verifier runs networkless and non-root.

### INFRA-G-REPRODUCIBILITY

- release preflight rejects byte drift;
- exactly one terminal lifecycle;
- RunRecord v2 binds every identity, budget, job, checkpoint, seal, and result;
- invalid-run ledger is automatic and complete;
- NS/WS pair differs only by skill treatment.

Formal execution is enabled only when all five gates are mechanically true.

## 15. Deliberate Non-Goals for v2 Activation

- Introducing Temporal, Kubernetes, or a second database service;
- cross-model automatic fallback in counted runs;
- making Case 042 benchmark-valid without its scientific G5–G12 evidence;
- changing scientific thresholds to make current Agent outputs pass;
- renaming every Case directory or replacing TOML serialization in one step;
- treating a higher turn ceiling as a substitute for durable recovery.

## 16. External Design References

- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/):
  resumable `RunState`, finite/optional turn limits, sessions, tracing, and
  usage accounting.
- [Anthropic long-running harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
  and [Managed Agents](https://www.anthropic.com/engineering/managed-agents):
  incremental sessions, structured progress, append-only session state, and
  separation of harness/session/sandbox.
- [Temporal](https://temporal.io/) and
  [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview): durable
  activities, checkpoints, timers, and bounded retry around failure-prone
  external operations.
- [AWS retry guidance](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)
  and [Google retry guidance](https://docs.cloud.google.com/storage/docs/retry-strategy):
  retry only transient and idempotent operations, with exponential backoff,
  jitter, and a hard retry/time budget.
- [Anthropic infrastructure-noise study](https://www.anthropic.com/engineering/infrastructure-noise):
  runtime resources and time limits are first-class experimental variables and
  must be frozen and reported.
- [SWE-bench harness](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/docker_setup.md):
  layered immutable environments and isolated per-instance evaluation.

## 17. Adversarial Design Review

The design is not approved for implementation until each attack below has an
explicit control and a failing-before-fix acceptance test.

| Adversarial question | Failure exposed | Required control |
|---|---|---|
| Can Candidate call Gateway HTTP directly instead of the CLI? | host-file access, cross-run job control | containment and ownership enforced inside Gateway |
| Can a valid symlink become a regular file before quarantine? | policy bypass and hidden-byte import | source `lstat`, no dereference, shared node policy |
| Can a run claim an old release while reading edited instructions? | false provenance | release-manifest digest preflight before API use |
| Can NS/WS use different turns, context policy, concurrency, or endpoint? | treatment confounding | resolved-lock pair comparison over every field |
| Can a 504 be retried by both SDK and Harness? | retry amplification and inconsistent cost | exactly one provider-boundary retry owner |
| Can retry/resume submit the same Slurm job twice? | duplicate compute and mixed artifacts | durable activity ID plus Gateway idempotency key |
| Can API retry or scheduler polling consume Agent turns? | score depends on service latency | separate activity budgets and coordinator wait |
| Can a resumed process silently use a changed profile? | model/API/budget drift | exact resolved-lock digest match or new run |
| Can a model/API secret reach generated code? | credential theft | provider client outside Candidate; secret scan |
| Can a Formal run switch endpoints after an outage? | deployment identity drift | no Formal endpoint/model fallback |
| Can resource-rich arms receive systematically better conditions? | infra noise masquerades as skill effect | frozen enforcement, blocked pair scheduling, latency reporting |
| Can a lifecycle contain `COMPLETED` followed by failure? | invalid immutable record | one terminal event plus state-machine replay |
| Can an Infra failure be written as reward zero? | denominator contamination | fail-closed result taxonomy and automatic invalid ledger |
| Can mounting the submission at `/app` hide the image's `/app/.venv`? | Local verifiers fail or execute Candidate Python | Verifier tools under `/opt`, migrate legacy test paths |
| Can one arm consume more context/tokens despite equal turns? | unequal inference budget | frozen context policy and total-token ceiling |
| Can 042 be promoted because code exists but no real reference run exists? | unvalidated benchmark release | G5–G12 evidence-derived promotion only |
| Can a new Case require editing Common Core or adding an ID branch to a shared plugin? | architecture regresses to identity dispatch | registry extension test with a dummy category/case and source scan |

The implementation plan must preserve this table as a traceability matrix: each
row maps to one numbered task and at least one exact automated test.
