---
name: bench-compute-request
description: Decide whether a scientific step needs external compute, choose an abstract cpu/gpu compute class, and write a validated structured compute request. Use for any step that is too expensive for the local workspace or requires a GPU. This skill only produces a request descriptor — it never submits or manages scheduler or cloud jobs.
---

# bench-compute-request

This skill converts a **scientific need** into a **structured, verifiable
request**. It never touches a real scheduler, cluster, or cloud account. It
does not know or emit cluster names, accounts, partitions, SSH, credentials,
or instance-management commands — those belong to the operator, who maps an
approved abstract request onto a real platform.

The boundary is strict:

```text
You (Claude Code):   decide what compute is needed and why
bench-compute-request: turn that decision into a validated request descriptor
Operator:            map the abstract request to a real CPU/GPU platform
```

## What this skill does

Three things, in order.

### 1. Decide whether external compute is needed at all

Before writing any request, ask whether the step can run locally in the
workspace:

- Reading and parsing existing outputs → no external compute.
- Small preprocessing / serial analysis / plotting → local.
- Anything that needs a trained model not yet present, a long simulation, or
  hardware this workspace does not have → external compute.

A step that only inspects existing results must **not** produce a request.

### 2. Choose a compute class: `cpu` or `gpu`

Read `references/compute-capabilities.json` for the abstract capability
levels available to this run. The manifest describes what each class is
suitable for and its resource ceilings. It deliberately does not name a
backend.

Choose the **lowest-cost class that can actually do the job**:

| Task | Class |
|---|---|
| small data processing, serial analysis | cpu |
| MPI CPU simulation, verification of existing runs | cpu |
| DeePMD training / JAX training / GPU molecular dynamics | gpu |
| small result parsing, reading existing output | no external compute |

Justify GPU use scientifically (e.g. "this phase trains a neural-network
potential over N frames; CPU training exceeds the time budget"). "Faster" is
not a reason by itself.

### 3. Write a validated request descriptor

Write one file per request:

```text
compute-requests/request-001.json
```

Follow `references/request-schema.md` exactly. The schema is enforced by the
operator's validator (`ccbench mvp compute-check`) and is fail-closed: a
request that violates the mechanical constraints is rejected, not repaired.
Every input file must be named with its exact SHA-256; every output path must
resolve under `compute-results/`. Provide `validation.success_markers` /
`validation.reject_if` so the operator and you know what a successful run
looks like.

## Hard guardrails

- **Never** run or emit a scheduler or cloud-management command. This skill
  only writes a request descriptor; the operator is the only party that ever
  submits, runs, copies, or cancels work.
- **Never** reference or invent a platform, cluster, account, partition, host,
  session, or API credential. You are not given them and must not need them:
  the operator maps your abstract request onto a real platform.
- **Never** fabricate a result. A request is a proposal; the outcome comes
  only from the operator returning the declared outputs under
  `compute-results/`.
- **Never** fabricate a digest. Compute SHA-256 from the actual input file.
- CPU requests must set `gpus = 0` and must not set a GPU memory floor.
  GPU requests must set `gpus >= 1` and a `minimum_gpu_memory_gb >= 1`.
- When a request is returned `REJECTED` or `FAILED`, read the real reason and
  revise — at most two resource revisions per step. Adjust batch size / memory
  / class as the evidence dictates; resume from checkpoints when available.
  Never blind-retry endlessly, and never claim success from a scheduler
  completion alone.

## Failure and verification mindset

- Scheduler state `COMPLETED` is not a scientific verdict. Inspect the actual
  engine output: convergence, NaN, model validity, error metrics, MD
  stability, output completeness — whatever the task demands.
- Treat `references/resource-guidance.md` as the decision aid for resource
  sizing and cost consciousness.
