# MatClaw 031–033 Strict Construction Design

## Objective

Construct Cases 031–033 as reproducible, fail-closed scientific benchmarks. A
case may become `benchmark_valid=true` only after two paper-profile runs from a
frozen commit and GPU image pass independent artifact verification, reproduce
the source result within an explicit tolerance, and agree with one another.

The currently running 032 and 033 jobs are diagnostic only. They must continue
undisturbed, but their outputs cannot satisfy a formal gate because their code
and image were not frozen before launch.

## Current Problems

1. Case 031 claims `benchmark_valid=true`, but the two paper evidence paths in
   `VALIDATION.json` are absent. The surviving fresh paper artifact ends at
   `0.1248839 eV/angstrom` with
   `no_informative_configurations_in_selection_band`, so it does not satisfy
   the `<0.10 eV/angstrom` stopping target.
2. `benchmark_valid.json` and `VALIDATION.json` can be edited independently of
   the evidence they claim. Validation is not currently fail-closed.
3. The task images contain only `public/`, while the reference programs live in
   `solution/`. This is correct for agent isolation, but no single reference
   runner stages inputs, mounts the hidden solution, and executes it
   reproducibly. The current `solution/solve.sh` files are not self-contained
   image entry points.
4. Case 033 exposes the full paper `search_path` in
   `public/run_profiles.json`, including the source best region. This leaks the
   answer to a task that is supposed to test adaptive search.
5. GPU support exists only as an unverified Dockerfile and runner. The current
   metadata says `gpus=0`, while validation prose defers formal work to a GPU
   host. GPU allocation, image identity, and CPU/GPU numerical parity are not
   one enforced contract.
6. Root tests pass without checking that claimed evidence exists, that the
   reference runner works, or that a public input leaks no answer.
7. The worktree contains extensive unrelated and uncommitted changes. A formal
   run from it would not have a stable source identity.

## Scope

In scope:

- Cases `031-matclaw-cips-active-distillation`,
  `032-matclaw-cips-curie-temperature`, and
  `033-matclaw-cips-domain-wall-search`.
- The CPU and GPU MatClaw images, the GPU runner, and the minimum `eval.py`
  changes required to allocate a GPU.
- Public isolation, hidden reference runners, independent verifiers, evidence
  manifests, validation derivation, diagnostic capture, paper runs, and final
  comparison.

Out of scope:

- Case 034 and AI2Kit.
- Portable skill migration except where an existing change is strictly needed
  by `eval.py` for these three cases.
- Unrelated ChemGraph, CP2K, documentation, and paper-file changes.

## Architecture

Each case has five boundaries:

```text
public inputs
    -> hidden reference solution
    -> raw artifacts
    -> independent verifier
    -> derived validation state
```

`public/` contains only inputs an agent is allowed to see. `solution/` contains
the official and alternative implementations and is never copied to `/app`.
`tests/verifier.py` independently recomputes scientific quantities from raw
artifacts. `reference/` contains provenance, acceptance policy, and generated
comparison summaries. A repository-level validation command derives
`VALIDATION.json` and `benchmark_valid.json`; humans do not set gate booleans.

The reference runner creates a temporary workspace, copies `public/` into it,
mounts that workspace at `/app`, mounts `solution/` read-only at `/solution`, and
executes `/solution/solve.sh`. This preserves public isolation while making the
reference workflow executable and testable.

## Evidence Model

Evidence is divided into two classes:

- `diagnostic`: current in-flight or pre-freeze runs. These may reveal
  convergence, performance, and implementation defects but never satisfy G7,
  G8, or G12.
- `formal`: exactly two runs per case from one clean commit and one immutable GPU
  image digest. Runs use declared independent seeds and do not share trajectories,
  models, or derived results.

Every formal run manifest records:

- case, profile, seed, run ID, start/end timestamps, and exit status;
- Git commit and a clean-worktree assertion;
- CPU verifier image digest and GPU execution image digest;
- GPU model, driver, CUDA, TensorFlow, DeePMD, ASE, and LAMMPS versions;
- SHA-256 for every public input, raw artifact, result, and verifier report;
- the exact command and environment contract;
- the independent verifier result.

Large raw artifacts remain in the configured artifact store. Committed evidence
manifests are only valid when the referenced artifact bundle is present and its
content hash matches. Missing evidence forces `benchmark_valid=false`.

## GPU Contract

The formal image is `dftworld-base-matclaw-cips:2.2.11-gpu` and is referenced by
digest after build. It must not be silently retagged as the CPU image. The task
runtime requests at least one NVIDIA GPU, and the Docker backend passes an
explicit GPU device request.

Before paper execution, the GPU host must pass:

1. `nvidia-smi` and TensorFlow both identify a GPU.
2. CPU/GPU energy difference is below `1e-6 eV` on the locked structure.
3. CPU/GPU maximum force-component difference is below
   `1e-6 eV/angstrom`.
4. A 100-step GPU MD produces finite energy, forces, positions, and a readable
   trajectory with the expected atom/frame counts.
5. A measured performance probe produces a timeout estimate; agent and formal
   run timeouts are at least 1.5 times the measured estimate.

The CPU image remains the verifier and development runtime. Formal execution and
verification image digests are recorded separately.

## Case-Specific Acceptance

### 031: Active Distillation

- The paper run uses the locked 3x3x1 system, teacher model, train/test split,
  two-member student committee, and `0.05–0.15 eV/angstrom` selection band.
- At least one real exploration-selection-label-retrain iteration must complete.
  Iteration zero convergence alone does not demonstrate the benchmarked active
  workflow.
- Training data must grow by exactly the number of selected, teacher-labelled,
  train/test-disjoint configurations.
- Final held-out force MAE is `<0.10 eV/angstrom` and within 25% relative error
  of the source `0.098 eV/angstrom`.
- Both formal runs satisfy the same gates; their final MAE difference is no more
  than `0.01 eV/angstrom`.

### 032: Curie Temperature

- A 6x6x1, 360-atom system runs the locked 350 K pilot and all 13 production
  temperatures with 2 fs steps and the paper frame intervals and durations.
- The pilot and each production trajectory meet exact frame-count and convergence
  contracts. Partial trajectories remain resumable but are never formal.
- The verifier unwraps periodic fractional z coordinates and independently
  recomputes `Q(T)=mean(abs(eta))`, both Tc estimators, and uncertainty.
- Each formal Tc is within `261.3 +/- 10 K`; the two formal Tcs differ by no more
  than 10 K.

### 033: Domain-Wall Search

- Public inputs disclose only the start point, search bounds, maximum two jobs
  per iteration, field protocol, and success metric. They do not disclose the
  future search path or best condition.
- The paper run contains seven sequential decision rounds and no more than two
  jobs per round. A new round may be proposed only after the previous round's
  raw trajectories have been analyzed.
- The field calculator implements the locked `F_i=q_i E` and
  `U=-sum_i(q_i r_i dot E)` protocol, with CPU/GPU parity.
- The verifier independently detects Cu flips, fits mean absolute delay over
  site distances 1–10, and requires slope `>0.3 ps/site`.
- The best region is `Ez=-0.16 +/- 0.02 V/angstrom`, `T=50 +/- 20 K`; both formal
  runs must show sequential propagation and agree on slope within
  `0.05 ps/site`.

## Validation State Machine

The state sequence is:

```text
constructed
  -> smoke_validated
  -> gpu_validated
  -> formal_run_1_valid
  -> formal_run_2_valid
  -> benchmark_valid
```

Any missing file, digest mismatch, verifier failure, numerical disagreement,
dirty source state, or incomplete second run returns the case to the last valid
lower state. `benchmark_valid.json` is generated from `VALIDATION.json`, never
maintained independently.

## Test Strategy

- Repository contracts: layout, public isolation, no leaked 033 path, profile,
  resource, timeout, and immutable input hashes.
- Reference-run integration: build the case image, stage public inputs, mount
  hidden solution, and complete each smoke workflow.
- Scientific unit tests: 031 configuration identity/deviation/data growth; 032
  unwrapping/order parameter/Tc; 033 electric field/flip detection/domino fit.
- Hidden verifier tests: clean valid artifact and forged/missing/tampered
  artifacts.
- GPU tests: visibility, CPU/GPU parity, short MD, short training, performance.
- Formal tests: complete paper artifacts, source comparison, two-run
  reproducibility, and fail-closed validation derivation.
- Final checks: full root tests, three case tests in the pinned image, shell and
  JSON validation, `git diff --check`, clean scoped worktree, and review.

## Execution Order

1. Preserve current jobs as diagnostic and inventory the current worktree.
2. Isolate only the MatClaw scope in a clean worktree and invalidate stale 031
   claims.
3. Implement the reference runner and fail-closed validation state machine.
4. Remove the 033 public answer leak and harden all hidden verifiers.
5. Validate the GPU image and GPU allocation path.
6. Repair and validate 031, then run two formal 031 executions.
7. Repair and validate 032, then run two formal 032 executions.
8. Repair and validate 033, then run two formal 033 executions.
9. Generate comparison reports and derive final status.
10. Run full verification and integrate the clean branch.

## Time and Resource Budget

- Design, isolation, contract repair, and CPU tests: 4–8 hours.
- GPU image build, compatibility work, and parity gates: 2–6 hours.
- One formal pass of all three cases: target 6–12 hours after the GPU probe.
- Two formal passes, analysis, reruns, and review: plan for 18–30 hours.

These are planning ranges, not acceptance evidence. The GPU performance probe
sets the final timeout and schedule.
