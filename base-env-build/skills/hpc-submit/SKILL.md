---
name: hpc-submit
description: Drive remote HPC jobs through the bench-hpc descriptor gateway — capabilities, request, submit, status/logs, attempt recovery, explicit fetch. Use for submitting computations to the batch system behind the gateway, monitoring them, diagnosing failures into new attempts, and retrieving outputs.
---

# HPC Submit (bench-hpc)

Use only after the engine skill's scientific preflight passed. Scheduler state (`SUCCEEDED`/`FAILED`) describes the process; only the engine's parser decides whether the calculation succeeded.

All scheduler contact goes through one trusted entry: the `bench-hpc` CLI talking to your run's gateway. You never see hosts, accounts, partitions, modules, or file-transfer commands — the gateway owns those. Your job is the *descriptor lifecycle*:

```text
capabilities → local scientific preflight → request → submit
    → status/logs → diagnose → new attempt (if needed)
    → fetch → engine parser → next stage
```

## The seven operations

| Command | Purpose |
|---|---|
| `bench-hpc capabilities` | What this run may do: abstract resource classes and ceilings. Read it before sizing anything. |
| `bench-hpc submit JOB_FILE OPERATION_ID ATTEMPT` | Submit a validated job descriptor under explicit `(OPERATION_ID, ATTEMPT)` identity. |
| `bench-hpc status OPERATION_ID [--attempt N]` | Attempt states for an operation (latest unless `--attempt`). |
| `bench-hpc logs OPERATION_ID [--attempt N]` | stdout/stderr of the attempt. |
| `bench-hpc fetch OPERATION_ID [--attempt N]` | Explicitly retrieve declared outputs into the workspace. Nothing enters the submission until you fetch it. |
| `bench-hpc cancel OPERATION_ID [--attempt N]` | Cancel a queued/running attempt. |
| `bench-hpc usage` | Run-scoped resource usage so far. |

Configuration comes from the environment (`BENCH_HPC_GATEWAY_URL`, `BENCH_HPC_RUN_TOKEN`, `BENCH_HPC_RUN_ID`) — never edit them; they are scoped to your run.

## Procedure

1. **Read capabilities first.** Size resources within the advertised ceilings; asking beyond them is rejected at the gateway.
2. **Run the engine's local scientific preflight** (structure gates, input sanity) before writing any job descriptor. Never submit from structure-generation code.
3. **Write the job descriptor** (YAML): declare `compute_class` (`cpu` or `gpu`) to route to the appropriate execution backend, name the approved runtime capability token (e.g. `cp2k`, `deepmd`, `jax`), provide a pure argv command, typed resources, content-addressed inputs (each input lists its path, sha256, size), and relative output paths. Concrete image digests and site names stay server-side. Absolute paths, `..`, shell strings, and scheduler flags are rejected by construction.
4. **Submit with a stable identity.** `OPERATION_ID` names the logical step (e.g. `scf-round-01`); `ATTEMPT` starts at 1.
5. **Monitor with status/logs**, not assumptions. Active execution walltime and GPU seconds count against your run budget. Check `bench-hpc usage` periodically to monitor consumption.
6. **Diagnose before retrying.** A terminal scheduler state is not a verdict: run the engine parser on fetched outputs to decide success.
7. **New attempts are explicit increments.** After a genuinely diagnosed failure (OOM, timeout, convergence), resubmit the same `OPERATION_ID` with `ATTEMPT+1` and change exactly one thing. Attempts never skip numbers, never run concurrently, and never repeat — the gateway enforces this.
8. **Fetch explicitly, then parse.** Only fetched files enter the submission. `fetch` refuses undeclared paths and never follows suspicious links.
9. **Record** operation id, attempt, job id, and what changed between attempts in your notes before moving to the next stage.

## Hard guardrails

- **Never guess site facts.** If capabilities say nothing about something you need, ask the operator instead of inventing partitions/modules/paths.
- **No blind resubmission.** diagnose → change one thing → new attempt → record what changed. A transport-level duplicate submit returns the same job; that is the only case where "resubmitting" is safe.
- **Scheduler success != scientific success.** `SUCCEEDED` means the process ran; the engine parser decides.
- **Explicit fetch only.** Outputs enter the submission solely through `bench-hpc fetch`.
- Stay inside your run workspace; no secrets or licensed file contents in logs.

## Where to find what

| Situation | Go to |
|---|---|
| writing/updating the job descriptor, resource sizing, monitoring patterns | `references/running.md` |
| execution-side preflight; what to record at submission | `references/validation.md` |
| pending forever, OOM-kill, TIMEOUT, failed attempts, when a new attempt is justified | `references/errors.md` |
| working descriptor example | `examples/execution-request.yaml` |
