# Running jobs through bench-hpc

## The descriptor, not a script

You write a **job descriptor** (YAML), never a scheduler script. The gateway
renders scheduler headers and the contained runtime command itself; anything
you try to smuggle in (scheduler flags, extra mounts, host paths) is rejected.

Descriptor fields (see `examples/execution-request.yaml`):

- `runtime` — image reference pinned to a content digest. Tags are rejected;
  a mutable image is not reproducible science.
- `command` — pure argv array. No shell strings, no empty entries.
- `resources` — typed: cpus, memory_gb, gpus, walltime_minutes. Stay within
  the ceilings `bench-hpc capabilities` advertised.
- `inputs` — content-addressed: each entry lists a workspace-relative `path`,
  its `sha256`, and `size_bytes`. The gateway re-verifies every byte at
  staging; a file changed after you computed the digest fails the submission
  instead of silently submitting different bytes.
- `outputs` — relative globs declaring what may be fetched later.
- `environment` — plain string variables; container-control names are rejected.

## Resource sizing

Read `bench-hpc capabilities` first. It exposes abstract resource classes
(cpu/gpu) with ceilings — not partition names. Size within them:

- Prefer the smallest resource class that fits the science; a failed
  oversized attempt wastes a real attempt slot.
- Walltime is a hard kill. Estimate from the engine's preflight, add margin,
  and let checkpointing (if the engine supports it) absorb the tail.

## Monitoring

- `bench-hpc status OPERATION_ID` returns every attempt's state plus the
  latest. Poll on a human-ish cadence; queue wait is external and free.
- `bench-hpc logs OPERATION_ID --attempt N` for stdout/stderr.
- `bench-hpc usage` for run-scoped consumption so far.

## Attempts and recovery

- Attempt 1 first; the next attempt is `ATTEMPT+1` after the predecessor
  reached a terminal state. The gateway rejects gaps, concurrency, repeats.
- Between attempts: read the logs, run the engine's own diagnosis, change
  exactly one thing, record what changed.
- A duplicate transport submit (same operation + attempt) returns the
  original job with `duplicate: true` — safe, no double scheduling.

## Fetching

`bench-hpc fetch OPERATION_ID [--attempt N]` copies declared outputs into the
workspace. It refuses undeclared paths, never overwrites, and treats archives
as opaque bytes. After fetching, hand the outputs to the engine parser — the
scheduler state alone is never the verdict.
