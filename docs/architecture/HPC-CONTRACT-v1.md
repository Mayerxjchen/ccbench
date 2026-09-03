# HPC Execution Contract (v1)

**Status:** Normative. The Candidate-facing HPC surface and the gateway-facing
job semantics.

## The Candidate sees only `bench-hpc`

- The Candidate **never receives** a private key, SSH agent socket, raw site
  configuration, or a scheduler command surface (no `ssh`, no `sbatch`/`qsub`
  from the Agent).
- The only HPC surface the Candidate receives is the **`bench-hpc`** client, a
  run-scoped bearer token, and a gateway URL.
- The trusted gateway owns site credentials and the scheduler transport.

## Job states

```python
job_states = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"}
```

## `bench-hpc` commands

```
bench-hpc capabilities
bench-hpc submit job.yaml
bench-hpc status <job-id>
bench-hpc logs <job-id>
bench-hpc fetch <job-id> output/
bench-hpc cancel <job-id>
bench-hpc usage
```

- All commands speak JSON to `BENCH_HPC_GATEWAY_URL` with bearer auth from
  `BENCH_HPC_RUN_TOKEN`. The client contains no SSH or scheduler logic.
- `submit` is idempotent: a duplicate `idempotency_key` returns the original
  job instead of launching twice.

## Job contract

- `runtime` names a **capability** (`cp2k`, `ai2kit`, `deepmd-jax`, ...); the
  gateway resolves it against locked runtime lock files into a
  `ResolvedRuntime` and seals the spec in `capability@sha256:<digest>` form
  before the adapter sees it. Agents never supply SIF paths or digests; the
  legacy digest-shaped form is verified as an assertion against the lock
  (hidden compatibility path, removal in Phase 8). Unknown capabilities fail
  closed, and `capabilities` advertises the site's `runtime_capabilities`.
- `command` is always an argv array — never a shell string.
- Absolute input/output paths and `..` traversal are rejected.
- `resources` (cpus, memory_gb, gpus, walltime_minutes) are validated against
  the active resource profile; the gateway clamps to the profile ceiling.
- `outputs` globs may not escape the job workspace.
- Remote paths are namespaced `<workspace_root>/<case_id>/<run_id>/<job_id>/`.

## Gateway rules

- Capabilities are **run-scoped**: token/run mismatch, expired tokens, and
  out-of-scope operations are rejected.
- The gateway, not the Candidate, owns the adapter object and the quota ledger.
- Settlement (waiting/cancelling outstanding jobs, revoking the token) happens
  before Candidate destruction; the Candidate cannot resume HPC actions
  afterwards.
- Accounting records requested vs used resources; secret-free site-config
  digests are stored, never config contents.
