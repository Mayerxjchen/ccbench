# Case 034 Heterogeneous Resource Evaluation Design

**Status:** Approved design, pending implementation-plan review.

## Goal

Evaluate whether PAgent can discover abstract HPC capabilities and make sound
heterogeneous resource decisions while developing a scientifically valid
DeePMD water potential. Scientific validity remains hardware-agnostic;
resource engineering is scored independently.

This design replaces the accidental Candidate-side CPU-only policy without
discarding the validated hidden CPU reference. The reference remains a
constructive proof that Case 034 is solvable, not a normative execution recipe.

## Evidence Behind the Design

The real project under the local, untracked `ai2kit/` reference uses the
resource split expected for this workflow:

- DeepMD committee training requests one GPU;
- LAMMPS exploration with the DeePMD committee requests one GPU;
- CP2K geometry optimization, AIMD, and active-learning labels use CPU jobs;
- ai2-kit and OMB orchestrate the stage-specific Slurm jobs.

The benchmark repository already has the correct trust boundary: the Candidate
sees `bench-hpc`, not SSH credentials or raw scheduler commands. The remaining
problem is that the current capability and resource profiles cannot express
multiple logical compute classes or aggregate run budgets, while legacy Case
034 locks still freeze a CPU-only execution policy.

## Normative Principle

> Scientific correctness is hardware-agnostic within the validated Compute
> Runtime and Resource Profile. Hardware selection and heterogeneous scheduling
> are evaluated separately as resource-engineering competence. Accelerator use
> is rewarded when compatible resources are available, but accelerator use is
> not a validity requirement. Evidence-backed fallback is permitted.

Consequences:

- a scientifically correct all-CPU result is still `VALID_RESULT`;
- an all-CPU result loses resource-engineering points when a compatible GPU was
  demonstrably available and no justified fallback exists;
- a documented GPU incompatibility followed by a safe CPU fallback does not
  trigger `SCIENTIFIC_FAIL`;
- queue names, module names, GRES spelling, accounts, hostnames, credentials,
  and absolute scratch paths are Site Adapter concerns and are never scored
  Candidate knowledge.

## Trust Boundary

```text
Candidate sandbox
    |
    | bench-hpc capabilities / submit / status / logs / fetch / usage
    v
Trusted gateway
    |
    | logical capability + runtime -> site-specific scheduler wiring
    v
Site Adapter
    |
    +-- CPU DFT jobs
    +-- GPU ML training jobs
    +-- GPU ML exploration jobs
    +-- CPU control/data jobs
```

The Candidate receives only:

- the public Case 034 bundle;
- the `bench-hpc` client and run-scoped bearer capability;
- abstract capability descriptions and remaining budget;
- generic scientific skills in the With-Skill arm.

The Candidate never receives:

- an SSH key, SSH agent socket, or raw `ssh` command path;
- site configuration or scheduler credentials;
- `sbatch`, `squeue`, `sacct`, partition names, module paths, or GRES syntax;
- hidden reference coordinates, outputs, thresholds, or execution scripts.

## Capability Contract

`bench-hpc capabilities` must expose logical compute capabilities rather than
Slurm trivia. The response has a versioned shape equivalent to:

```yaml
contract_version: hpc-capabilities/v2
capability_classes:
  - id: cpu-dft
    kind: cpu
    runtime_ids: [cp2k-blyp-d3]
    limits:
      max_cpus: 16
      max_gpus: 0
      max_memory_gb: 32
      max_walltime_minutes: 2880

  - id: gpu-ml
    kind: accelerator
    accelerator:
      api: cuda
      devices_per_job: 1
    runtime_ids: [deepmd-cuda, lammps-deepmd-cuda]
    limits:
      max_cpus: 8
      max_gpus: 1
      max_memory_gb: 32
      max_walltime_minutes: 1440

  - id: cpu-control
    kind: cpu
    runtime_ids: [ai2kit-control]
    limits:
      max_cpus: 4
      max_gpus: 0
      max_memory_gb: 16
      max_walltime_minutes: 240

budget:
  max_concurrent_jobs: 20
  max_total_jobs: 320
  max_cpu_hours: 4096
  max_gpu_hours: 512
  max_storage_bytes: 214748364800
  max_run_walltime_hours: 336
```

The public response may describe accelerator API, device count, runtime
compatibility, and generic resource ceilings. It must not expose site queue,
partition, account, module, filesystem, or credential details.

## Job Contract

The v2 HPC JobSpec extends the existing immutable argv-based contract with:

```yaml
schema_version: 2
idempotency_key: run-scoped-unique-key
stage_role: deepmd_train
capability_id: gpu-ml
runtime_id: deepmd-cuda
command: [python, -m, workflow.train]
resources:
  cpus: 4
  memory_gb: 16
  gpus: 1
  walltime_minutes: 240
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

`stage_role` is one of:

- `structure_prepare`;
- `cp2k_geopt`;
- `cp2k_aimd`;
- `deepmd_train`;
- `lammps_explore`;
- `screen_candidates`;
- `cp2k_label`;
- `validate_model`;
- `other`, which requires a Candidate justification in provenance.

The gateway validates capability/runtime compatibility and aggregate budget
before submission. The Site Adapter maps the logical request to concrete
partition, GRES, modules, container/runtime image, launcher, and workspace.
The Candidate cannot supply or override those site fields.

`workload` is mandatory for JobSpec v2. It contains non-negative integers;
`al_round` is zero for non-AL work and 1-5 for an AL stage. The gateway sums
the declared counters against `science_budget` before submission. Trusted
post-run parsers independently derive label, AIMD, training, exploration, and
validation counts from fetched artifacts. Under-declaration is an invalid
submission/resource violation, never a way to escape the ceiling.

## Case 034 Resource Budget

The Resource Profile defines ceilings, not a required execution recipe:

```yaml
hpc:
  max_concurrent_jobs: 20
  max_total_jobs: 320
  max_cpu_hours: 4096
  max_gpu_hours: 512
  max_storage_bytes: 214748364800
  max_walltime_minutes_per_job: 2880
  max_run_walltime_hours: 336

science_budget:
  max_dft_labels: 190
  max_cp2k_aimd_steps: 10000
  max_committee_models: 4
  max_training_steps_per_model_per_round: 400000
  max_training_model_steps: 5600000
  max_exploration_steps_per_trajectory: 100000
  max_exploration_md_steps: 2500000
  max_validation_md_steps: 5000
  max_active_learning_rounds: 5
```

The budget ceiling follows the complete five-round expert lineage rather than
the temporary two-round CPU construction profile: a 10000-step CP2K AIMD
mother trajectory, a four-model committee, 100k training steps in rounds 1-2,
400k in rounds 3-5, three then four exploration temperatures, and a maximum
label schedule of 20 + 20 + 50 + 50 + 50 = 190. With two exploration seeds,
the reference schedule represents approximately 2.43 million aggregate
LAMMPS MD steps; the 2.5-million ceiling leaves only rounding headroom.

These values are ceilings, not completion requirements. An Agent may stop
before round five when independent held-out metrics, declining model
deviation, configuration-space coverage, and physical validation jointly
support the decision. Consuming all five rounds without assessing whether more
labels are needed is not intrinsically better. Failed and retried jobs count
toward total jobs and consumed resources. Queue wait does not consume CPU/GPU
hours and is not scored as Agent efficiency.

## Expected Resource Strategy

The scientifically sensible, non-mandatory strategy is:

| Stage | Preferred capability | Rationale |
|---|---|---|
| Structure preparation | `cpu-control` | lightweight deterministic preparation |
| CP2K GEO_OPT/AIMD | `cpu-dft` | validated CPU DFT runtime |
| DeepMD committee training | `gpu-ml` | CUDA-compatible training acceleration |
| LAMMPS exploration/model deviation | `gpu-ml` | GPU-compatible DeePMD inference |
| Screening/data conversion | `cpu-control` | orchestration and data processing |
| CP2K active-learning labels | `cpu-dft` | independent first-principles labels |
| Final validation | matching runtime | chosen according to the validation task |

The Candidate must make and evidence the decision. The benchmark does not
inject fixed stage headers into the Candidate workspace.

## Scientific Validity and Result Taxonomy

Existing scientific hard gates remain independent of resource score:

- coordinates are generated by the Candidate with reproducible provenance;
- the CP2K AIMD mother data and active-learning labels have one consistent DFT
  fingerprint;
- the workflow materially uses ai2-kit;
- the model committee and active-learning rounds are real, not copied outputs;
- held-out energy/force accuracy and physical validation pass;
- final models and provenance are complete and independently verifiable.

Resource choice cannot convert a scientifically passing result into
`SCIENTIFIC_FAIL`. Infrastructure failures that prevent a fair capability
trial remain `INFRA_INVALID`; missing/invalid Candidate outputs remain
`AGENT_FAILURE`.

## Resource-Engineering Score

Resource engineering contributes 10 points without changing the scientific
hard gates:

| Criterion | Points | Trusted evidence |
|---|---:|---|
| Capability discovery | 2 | capability response recorded before planning/submission |
| Workflow decomposition | 2 | distinct stage roles and dependency graph |
| DeepMD accelerator use | 2 | compatible GPU runtime plus actual GPU allocation/backend |
| LAMMPS accelerator use | 2 | compatible GPU runtime plus actual GPU allocation/backend |
| CP2K resource choice | 1 | CPU DFT capability without meaningless GPU allocation |
| Budget/fallback/provenance | 1 | usage ledger, bounded retry, evidence-backed fallback |

Rules:

- Candidate prose alone is never evidence of accelerator use.
- GPU points require a trusted record showing requested and allocated GPU,
  compatible runtime identity, visible accelerator/backend, and successful
  stage output.
- When GPU capability is absent or its compatibility probe fails, a bounded
  CPU fallback with trusted evidence remains eligible for the corresponding
  points; an unsupported assertion of incompatibility is not enough.
- Allocating a GPU to CP2K without a compatible GPU runtime earns no CP2K
  resource-choice point and consumes the GPU budget.
- Repeated blind retries reduce the budget/fallback point and may independently
  trigger the existing resource-exceeded result class.

## Trusted Run Record

For every job the gateway records:

```yaml
job_id: job-0001
stage_role: deepmd_train
capability_id: gpu-ml
runtime_id: deepmd-cuda
requested: {cpus: 4, gpus: 1, memory_gb: 16, walltime_minutes: 240}
allocated: {cpus: 4, gpus: 1}
state_timeline: []
exit_status: 0
usage: {cpu_hours: 0.8, gpu_hours: 0.2, elapsed_seconds: 720}
runtime_provenance: {}
input_digest: sha256:...
output_digest: sha256:...
fallback_from: null
```

For a real Slurm backend, requested/allocated values and terminal state are
collected by trusted infrastructure from scheduler accounting. The Candidate
cannot author or edit these records. The immutable final Run Record includes
aggregate CPU/GPU hours, job count, storage use, runtime/profile digests, and
all fallback relationships.

## Hidden Reference Boundary

The existing hidden CPU reference remains valid as a constructive proof and a
scientific calibration source. It is not copied into the Candidate bundle and
does not define the Candidate's `capability_id`, runtime, or hardware path.

Reference thresholds must tolerate validated hardware-level floating-point
variation. Any threshold that is only reproducible on one CPU implementation
must be revised or explicitly classified as a platform-specific reference,
not silently imposed on heterogeneous Candidate runs.

The in-progress legacy G9 CPU reference work may complete for calibration and
historical evidence, but no CPU-only lock from that branch may be promoted into
the Candidate Resource Profile.

## Profile and Runtime Migration

Case 034 moves from one flat GPU/CPU declaration to four logical runtime
identities:

- `ai2kit-control`;
- `cp2k-blyp-d3`;
- `deepmd-cuda`;
- `lammps-deepmd-cuda`.

Each runtime is content-addressed and site-qualified. The Case Compute Runtime
lock records acceptable logical runtime IDs and their immutable identities;
the Platform Profile records which capability class serves each runtime. A
single CPU controller image tag is not a substitute for the four runtime
identities.

The Site Adapter owns the mapping to the school's current modules, images,
partitions, launchers, and scratch root. That mapping is versioned and hashed
in trusted infrastructure but not exposed to the Candidate.

## Candidate Interface

The instruction continues to say that remote HPC capability exists and that
the Agent must discover and use it. It must not prescribe GPU/CPU placement.
The Candidate is expected to:

1. call `bench-hpc capabilities`;
2. inspect compatible logical runtimes and the remaining budget;
3. generate the 64-water structure and workflow inputs;
4. submit stage-specific jobs with dependencies and unique idempotency keys;
5. inspect statuses/logs and perform bounded, evidence-backed recovery;
6. fetch only declared outputs;
7. retain a resource-decision record in the final submission.

No-Skill and With-Skill arms receive exactly the same capability contract,
runtime set, budget, site mapping, and run deadline. They differ only in
physical skill availability.

## Failure Handling

- Capability/runtime incompatibility is rejected before scheduler submission.
- Budget overflow is rejected before scheduler submission and recorded as a
  Candidate-visible quota error.
- Declared workload counters are charged before submission; trusted observed
  counters replace them at settlement, and any observed under-declaration is
  recorded as a resource violation.
- Duplicate idempotency keys return the existing job and never double-submit.
- A lost or uncertain job is reconciled by the gateway; the Candidate cannot
  bypass it with raw scheduler access.
- A fallback job must link to the failed capability probe or stage job.
- Site Adapter or gateway faults that invalidate the trial are
  `INFRA_INVALID`, not Agent or scientific failures.
- Candidate-chosen invalid resources, unbounded retries, or ignored failures
  remain Candidate failures or resource-score deductions according to the
  existing result taxonomy.

## Verification Strategy

The implementation must include:

1. schema tests for capability classes, v2 JobSpec, aggregate budgets, and
   forbidden site fields;
2. gateway tests for compatibility, idempotency, aggregate quota enforcement,
   settlement, and trusted usage records;
3. Site Adapter tests proving two different Slurm configurations produce the
   same Candidate-facing capabilities;
4. Candidate-image tests proving no SSH material or scheduler command is
   present;
5. 034 integration tests for CPU DFT, GPU training, GPU exploration, CPU
   fallback, all-CPU valid result, and budget exhaustion;
6. evaluator tests proving resource score cannot alter scientific validity;
7. mutation tests that remove accelerator evidence, falsify allocation,
   collapse stages, omit fallback linkage, or reintroduce CPU-only locks;
8. fake-adapter end-to-end tests before any real-site smoke;
9. one bounded real-site CPU capability smoke and one bounded GPU capability
   smoke through `bench-hpc`, with no raw Candidate SSH;
10. lock regeneration and deterministic reproduction checks.

Formal No-Skill/With-Skill ablation remains out of scope until the redesigned
Case 034 passes the release audit.

## Migration Boundaries

This work changes shared HPC infrastructure and Case 034 together. It must be
implemented in independently reviewable slices:

1. shared capability/JobSpec/accounting contracts;
2. trusted gateway and adapter mapping;
3. Case 034 profiles, runtimes, evaluator, and locks;
4. Candidate workflow integration and fake-adapter proof;
5. bounded real-site conformance and final release audit.

It must not:

- copy the untracked local `ai2kit/` reference into the benchmark;
- expose hidden expert structures or outputs;
- replace `bench-hpc` with raw SSH;
- invalidate or rewrite historical hidden CPU evidence;
- run the formal ablation experiment as part of migration.

## Release Criteria

The redesign is complete when:

- Candidate-facing capabilities contain no site trivia;
- the Site Adapter can map CPU DFT and GPU ML logical capabilities on the real
  site;
- aggregate budgets are enforced and reported;
- 034 can express and execute stage-specific CPU/GPU jobs through `bench-hpc`;
- scientific validity and the 10-point resource score are demonstrably
  independent;
- an evidence-backed CPU fallback remains valid;
- hidden reference identity remains sealed and constructive only;
- all schema, contract, integration, mutation, lock, and release-audit tests
  pass;
- the real-site CPU/GPU smoke evidence is frozen;
- no formal ablation data has been generated.
