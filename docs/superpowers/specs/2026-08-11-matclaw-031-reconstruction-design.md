# MatClaw Case 031 Reconstruction Design

## Objective

Reconstruct Case 031 as an honest active-distillation benchmark. A successful
paper run must demonstrate at least one real exploration, in-band selection,
teacher labelling, dataset growth, and committee retraining step before it may
claim a held-out force MAE below `0.10 eV/angstrom`.

All previous 031 run products and run records have been removed. The case starts
from `state=constructed` and `benchmark_valid=false`.

## Confirmed Failure Modes

1. `run_distillation.py` stops immediately when iteration-zero MAE is below
   `0.10`. That proves ordinary supervised training, not the active workflow.
2. When the first 200 K exploration batch has no committee deviation inside
   `[0.05, 0.15] eV/angstrom`, the workflow terminates instead of trying the
   remaining declared physical conditions.
3. Every exploration trajectory is driven by committee member zero. Member one
   is used only for retrospective deviation, reducing exploration diversity.
4. The run has no durable stage checkpoint. A timeout discards long teacher MD
   and training work or encourages unsafe reuse of unverified artifacts.
5. The old validation record accepted missing paths and two identical
   iteration-zero results. It did not prove independent active distillation.
6. The latest Agent attempt failed with an API timeout. This is a harness/API
   failure and must remain separate from the deterministic reference workflow.

## Chosen Approach

Keep the source selection band and scientific protocol fixed, but replace the
single-batch early exit with a bounded, predeclared exploration controller.
Within each active iteration it runs successive temperature batches, alternating
the committee member that drives MD, until it finds genuine in-band candidates
or exhausts the declared attempt budget. Exhaustion produces an invalid
diagnostic result; it never selects an out-of-band frame.

This is preferred over relaxing the deviation band or accepting iteration zero,
both of which would change the benchmark rather than repair it.

## Components

### Local Docker controller and HPC execution boundary

The Agent remains inside the local Case 031 Docker sandbox and acts only as a
controller. The controller process runs in that same local Docker container and
uses the Case 034 canonical surface: `stage`, `submit`, `status`, `log`,
`cancel`, and `fetch`. It wraps the existing `SshSlurmTransport` and reaches the
HPC login node directly from the container. Scientific MD and training never run
in the local controller container.

SSH authentication is forwarded into the local Docker controller through an
SSH-agent socket. The SSH config and known-hosts data are mounted read-only;
private-key bytes are not copied into the image or `/app`. The controller accepts
only the fixed `<site-alias>` target, the configured remote root, and the six
transport operations; the Agent is not given an arbitrary remote-shell field.

The confirmed execution target is SSH alias `<site-alias>` (`<site-user>` on login node
`<site-login-node>`). Real work is submitted to Slurm partition `gpu` with one GPU. A probe
allocated `<site-node-gpu5>` and reported `NVIDIA A100-SXM4-80GB`, driver `550.54.15`.
The login node has no GPU. GPU nodes expose Docker only to privileged users, so
formal work uses `/public/software/apptainer/bin/apptainer` version `1.4.0`
with `exec --nv`; it does not depend on Docker daemon access.

The control workspace is local. Inputs are checksum-staged with rsync into a
run-specific directory below `/public/home/<site-user>/dftworld2-runs/matclaw-031/`.
Only a `COMPLETED` state confirmed by `sacct` permits checksum fetch. `squeue` is
a non-terminal fallback, and an unknown query result is retried rather than
treated as success or failure.

The formal runtime is locked twice: the source OCI image is referenced by
`name@sha256:` and the resulting Apptainer SIF is recorded by file SHA-256.
Both formal runs use the identical SIF hash. The local Docker controller
restricts remote paths to the configured run root, resources to the approved
Slurm policy, and operations to the six controller methods.

### Pure active-selection contract

`solution/active_contract.py` contains dependency-light functions for inclusive
band selection, configuration de-duplication, batch planning, and dataset-growth
checks. These functions are unit tested without DeePMD or GPU execution.

Selection rules:

- a candidate is eligible only when `0.05 <= sigma <= 0.15`;
- its configuration hash is absent from training, held-out, and earlier selected
  hashes;
- selection order is deterministic: decreasing deviation, then configuration
  hash;
- at most `selection_cap` configurations are returned;
- an empty batch advances to the next declared batch; it never changes limits.

### Reference orchestrator

`solution/run_distillation.py` owns MD, teacher labels, DeePMD training, and
artifact creation. It accepts an explicit seed and resume flag. Paper mode
cannot terminate successfully before history contains iteration 0 plus at least
one retrained iteration.

Exploration batches are declared in `solution/run_profiles.json`. The paper
profile begins with 200 K and expands through 600 K, 1000 K, 350 K, 700 K, and
250 K conditions. The exact ordering is frozen before formal execution. MD
drivers alternate between committee members and use distinct seeds.

### Checkpoint and resume

`active_learning/checkpoint.json` records input hashes, profile hash, root seed,
completed stage, artifact hashes, history, and selected configuration hashes.
Every update is written to a temporary sibling and atomically renamed.

Resume is allowed only when structure, teacher, profile, seed, and every
completed artifact hash match. A mismatch fails closed. Two formal runs use
separate empty roots and therefore cannot resume from one another.

### Hidden verifier

The verifier independently reconstructs candidate frames from trajectories,
recomputes committee deviations from the two delivered models, verifies selected
hashes and teacher labels, and checks exact dataset growth. In paper mode it
rejects iteration-zero convergence, empty selections, missing checkpoint hashes,
out-of-band selections, duplicated frames, and a final MAE not recomputed from
the delivered model and held-out dataset.

### Alternative implementation

`alt_distillation.py` remains organizationally independent but implements the
same external artifact contract. It must pass the same smoke and negative
verifier suite. It is not used as evidence for either formal primary run.

## Execution and Evidence Flow

```text
Agent in local Docker sandbox
  -> in-container restricted controller CLI
  -> in-container SshSlurmTransport
  -> forwarded SSH agent authentication
  -> rsync stage + Slurm sbatch
  -> Apptainer --nv on A100
  -> checksum fetch after sacct COMPLETED
  -> clean public inputs
  -> teacher MD + fixed held-out set
  -> iteration-0 committee
  -> bounded multi-batch student MD
  -> independently recomputed committee deviation
  -> unique in-band selection
  -> teacher labels + exact dataset growth
  -> retrained committee
  -> final held-out MAE
  -> hidden verifier
  -> formal manifest and cross-run comparison
```

Reference execution is direct and local to the locked container; it does not
call an LLM API on the compute side. Agent evaluation is a separate usability
check and its 5xx or timeout cannot be promoted to scientific evidence.

## Failure Handling

- No in-band candidate after all batches: write a diagnostic failure report,
  keep `benchmark_valid=false`, and stop without fabricating data.
- Training/MD interruption: retain only atomically completed stages; resume
  after verifying all hashes.
- Non-finite MD, model duplication, train/test leakage, or teacher-label mismatch:
  verifier rejects the run.
- Dirty Git state, tag-only image, missing artifact, reused workspace, or same
  formal seed: formal validation rejects the run.
- MAE miss or cross-run disagreement: preserve the failed attempt as diagnostic
  evidence and start a new attempt only after a reviewed code/profile change.

## Test Strategy

1. Pure unit tests for band boundaries, deterministic ordering, de-duplication,
   batch exhaustion, and exact dataset growth.
2. Orchestrator tests with small fake frames/models for mandatory active steps,
   alternating model/seed batches, and atomic checkpoint identity.
3. Hidden-verifier negative fixtures for forged MAE, forged deviations,
   out-of-band selection, duplicate/leaky configurations, zero active iterations,
   tampered models, and incomplete artifacts.
4. CPU smoke runs of primary and alternative implementations.
5. GPU qualification and one paper-profile diagnostic run.
6. Two paper-profile formal runs from one clean commit and immutable image digest
   with distinct seeds and isolated workspaces.

## Acceptance

A single formal run passes only when:

- the 3x3x1 system and pinned teacher/input hashes match;
- at least one selection-label-retrain iteration completes;
- training growth equals the count of unique selected teacher-labelled frames;
- all selected deviations are in `[0.05, 0.15] eV/angstrom`;
- final held-out force MAE is `<0.10 eV/angstrom`;
- relative error from the source `0.098 eV/angstrom` is `<=25%`;
- the hidden verifier accepts all raw artifacts.

The benchmark becomes valid only when two formal runs also share the same clean
commit and immutable GPU image digest, use different seeds and workspaces, and
their final MAEs differ by no more than `0.01 eV/angstrom`.

## Resource Estimate

- Code and CPU test repair: 3–6 hours.
- GPU smoke, parity, and performance probe: 1–2 hours.
- One paper diagnostic: approximately 1–3 hours after the probe.
- Two formal runs and review: approximately 3–7 hours.

Measured GPU performance replaces these estimates before formal execution.
