---
name: hpc-submit
description: Abstract compute requests and gateway execution — decide whether a step needs external compute, choose an abstract cpu/gpu class, write a validated compute request (request-only), or drive remote jobs through the bench-hpc descriptor gateway (gateway-execution: capabilities, submit, status/logs, diagnosis, attempts, fetch, engine-parser handoff). Use for any compute that is too expensive for the local workspace or needs a GPU, and for submitting, monitoring, and retrieving those jobs.
---

# hpc-submit

One skill, two modes. They share one decision — **is this step CPU-shaped or
GPU-shaped, and what is the smallest shape that actually finishes it?** — and
one mindset — **a scheduler state is not a scientific verdict.** They differ in
how far the agent reaches toward the hardware.

```text
request-only       scientific need → decision → abstract compute request file
                             (never touches a cluster, gateway, or cloud account)

gateway-execution   validated descriptor → bench-hpc submit → status/logs
                             → diagnose → attempt+1 → fetch → engine parser
```

## Which mode?

| Situation | Mode |
|---|---|
| A step is too expensive for this workspace or needs a GPU, and the operator supplies results back into the workspace (`compute-results/`) | `request-only` |
| This run has a live `bench-hpc` gateway (`BENCH_HPC_GATEWAY_URL` set) and the step is meant to run against the batch system now | `gateway-execution` |

When both are available, prefer the channel the operator directed you to use
for this run. Never use `request-only` to smuggle a submission, and never use
`gateway-execution` to skip a local scientific preflight.

---

## Mode A — request-only

Decide whether external compute is needed, choose an abstract class, and write
a **validated request descriptor**. This mode produces a file; the operator is
the only party that ever submits, runs, copies, or cancels. It does not know or
emit cluster names, accounts, partitions, SSH, credentials, or instance
commands.

```text
You:               decide what compute is needed and why
request-only:      turn that decision into a validated request file
Operator:          map the abstract request to a real CPU/GPU platform
```

### Procedure

1. **Decide whether external compute is needed at all.** Parsing, plotting, and
   small serial analysis of already-returned outputs run locally and produce
   **no** request.
2. **Choose `cpu` or `gpu`** from `references/resource-guidance.md`, staying
   inside the ceilings in `references/compute-capabilities.json`. Choose the
   lowest-cost class that can actually do the job; justify GPU use
   scientifically ("this phase trains an NN potential over N frames; CPU
   training exceeds the time budget"). "Faster" is not a reason.
3. **Write one validated request per step:**

   ```text
   compute-requests/request-001.json
   ```

   Follow `references/request-schema.md` exactly. The deterministic validator
   is `ccbench mvp compute-check` (authoritative), with a shape-only JSON
   Schema at `schemas/compute-request.schema.json` for tooling. Validation is
   fail-closed: a request that violates the mechanical constraints is rejected,
   not repaired.
4. **When the operator returns outputs**, verify them against
   `validation.success_markers` / `validation.reject_if` before folding them
   into your work. A `COMPLETED` scheduler state is not a scientific verdict.

### Guardrails (request-only)

- **Never** run or emit a scheduler, gateway, or cloud-management command.
  This mode only writes a request file.
- **Never** reference or invent a platform, cluster, account, partition, host,
  session, or credential. The operator maps your abstract request.
- **Never** fabricate a result or a digest. A request is a proposal; compute
  SHA-256 from the actual input file.
- CPU requests: `gpus = 0`, no GPU memory floor. GPU requests: `gpus >= 1` and
  `minimum_gpu_memory_gb >= 1`. Memory is **per node**
  (`memory_gb_per_node`).
- Revisions are limited: a `REJECTED`/`FAILED` return allows **at most two
  resource revisions per step**, driven by the actual evidence. Never
  blind-retry, and never claim success from scheduler completion alone.

---

## Mode B — gateway-execution

Drive remote jobs through one trusted entry: the `bench-hpc` CLI talking to
your run's gateway. You never see hosts, accounts, partitions, modules, or
file-transfer commands — the gateway owns those. Your job is the *descriptor
lifecycle*:

```text
capabilities → local scientific preflight → request → submit
    → status/logs → diagnose → new attempt (if needed)
    → fetch → engine parser → next stage
```

Use only after the scientific preflight passed. Scheduler state
(`SUCCEEDED`/`FAILED`) describes the process; only the engine's parser decides
whether the calculation succeeded.

### The gateway operations

| Command | Purpose |
|---|---|
| `bench-hpc capabilities` | What this run may do: abstract resource classes, runtime capability tokens, ceilings. Read it before sizing anything. |
| `bench-hpc submit JOB_FILE OPERATION_ID ATTEMPT` | Submit a validated job descriptor under explicit `(OPERATION_ID, ATTEMPT)` identity. |
| `bench-hpc status OPERATION_ID [--attempt N]` | Attempt states for an operation (latest unless `--attempt`). |
| `bench-hpc logs OPERATION_ID [--attempt N]` | stdout/stderr of the attempt. |
| `bench-hpc fetch OPERATION_ID [--attempt N]` | Explicitly retrieve declared outputs into the workspace. Nothing enters the submission until you fetch it. |
| `bench-hpc cancel OPERATION_ID [--attempt N]` | Cancel a queued/running attempt. |
| `bench-hpc usage` | Run-scoped resource usage so far. |

Configuration comes from the environment (`BENCH_HPC_GATEWAY_URL`,
`BENCH_HPC_RUN_TOKEN`, `BENCH_HPC_RUN_ID`) — never edit them; they are scoped
to your run.

### Procedure

1. **Read capabilities first.** Size resources within the advertised ceilings
   and name only the runtime capability tokens it offers; asking beyond them is
   rejected at the gateway.
2. **Run the local scientific preflight** (structure gates, input sanity)
   before writing any job descriptor. Never submit from structure-generation
   code.
3. **Write the job descriptor** (YAML, `references/running.md`,
   `examples/execution-request.yaml`): declare `compute_class` (`cpu`|`gpu`),
   a runtime capability token offered by capabilities, a pure argv `command`,
   typed resources, content-addressed `inputs` (path, sha256, size), and
   relative output paths. Absolute paths, `..`, shell strings, and scheduler
   flags are rejected by construction.
4. **Submit with a stable identity.** `OPERATION_ID` names the logical step
   (e.g. `scf-round-01`); `ATTEMPT` starts at 1.
5. **Monitor with status/logs**, not assumptions. Active execution walltime
   and GPU seconds count against your run budget; check `bench-hpc usage`
   periodically.
6. **Diagnose before retrying.** A terminal scheduler state is not a verdict:
   run the engine parser on fetched outputs to decide success.
7. **New attempts are explicit increments.** After a genuinely diagnosed
   failure (OOM, timeout, divergence), resubmit the same `OPERATION_ID` with
   `ATTEMPT+1` and change exactly one thing. Attempts never skip numbers, never
   run concurrently, and never repeat — the gateway enforces this.
8. **Fetch explicitly, then parse.** Only fetched files enter the submission.
   `fetch` refuses undeclared paths and never follows suspicious links.
9. **Record** operation id, attempt, job id, and what changed between attempts
   in your notes before moving to the next stage.

### Guardrails (gateway-execution)

- **Never guess site facts.** If capabilities say nothing about something you
  need, ask the operator instead of inventing partitions/modules/paths.
- **No blind resubmission.** diagnose → change one thing → new attempt →
  record what changed. A transport-level duplicate submit returns the same
  job; that is the only case where "resubmitting" is safe.
- **Scheduler success != scientific success.** `SUCCEEDED` means the process
  ran; the engine parser decides.
- **Explicit fetch only.** Outputs enter the submission solely through
  `bench-hpc fetch`.
- Stay inside your run workspace; no secrets or licensed file contents in logs.

---

## Shared hard guardrails

- **No invented backends.** You name an abstract `cpu`/`gpu` class and a
  capability token from the manifest; a concrete host, image digest, or site
  name is never invented.
- **Input digests are real.** Compute SHA-256 from the bytes on disk and
  re-verify after any last edit. A stale digest is the protection working, not
  a malfunction.
- **Outputs stay confined.** request-only outputs resolve under
  `compute-results/`; gateway outputs resolve under declared relative paths.
- **Scheduler state is never the verdict.** request-only inspects returned
  engine output against `validation` markers; gateway-execution runs the engine
  parser on fetched output.
- **No endless retries.** request-only: at most two resource revisions per
  step. gateway-execution: an attempt is an explicit increment after a
  diagnosed change.

## Where to find what

| Situation | Go to |
|---|---|
| request-only: is external compute needed, cpu vs gpu, honest sizing | `references/resource-guidance.md` |
| request-only: request file schema, per-node resources, example requests | `references/request-schema.md` |
| request-only: abstract ceilings / allowed values for this run | `references/compute-capabilities.json` |
| gateway-execution: writing the job descriptor, monitoring patterns | `references/running.md` |
| gateway-execution: preflight + what to record at submission | `references/validation.md` |
| gateway-execution: pending forever, OOM-kill, TIMEOUT, failed attempts | `references/errors.md` |
| working job descriptor example | `examples/execution-request.yaml` |
