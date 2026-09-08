# CCBench MVP test infrastructure

This is the deliberately small workflow for case development. Claude Code runs
directly in a clean public workspace; it is not installed in the benchmark
runtime image. Hidden material is mounted only after the submission is sealed.

## Trust boundary

- The maintainer repository contains cases, verifiers, solutions, and references.
- `ccbench mvp export` creates a new workspace outside the repository from the
  case allowlist. Upload only that workspace to CompShare.
- Keep the sibling `*.lock.json` on the maintainer machine. It is the trusted
  copy used to detect changes to task/input/policy bytes after retrieval.
- Claude Code may write only `work/`, `final/`, `compute-requests/`, and
  `compute-results/`.
- Only `final/` is collected. The hidden verifier runs afterwards with no
  network and read-only mounts.

`CLAUDE.md` is a protocol rule and reduces accidental leakage. The real
anti-cheat boundary is that the exported workspace physically contains no
solution, reference, hidden verifier input, Git history, prior run, SSH key, or
CompShare management credential.

## One case, end to end

Use the repository virtual environment without changing directory into
`.venv`:

```bash
cd /Users/xjchen/bench/ccbench
source .venv/bin/activate
```

From the MVP runner worktree during development, export a case:

```bash
ccbench mvp export \
  --case 002 \
  --out /private/tmp/ccbench-runs/002-run-001
```

This also creates the operator-only lock:

```text
/private/tmp/ccbench-runs/002-run-001.lock.json
```

Upload only `002-run-001/` to the clean CompShare instance. Start Claude Code
inside that directory. Do not upload the lock, repository, verifier, solution,
reference, or cloud account configuration.

After retrieving the workspace, verify it and freeze `final/`:

```bash
ccbench mvp check \
  --bundle /private/tmp/ccbench-runs/002-run-001 \
  --lock /private/tmp/ccbench-runs/002-run-001.lock.json

ccbench mvp freeze \
  --bundle /private/tmp/ccbench-runs/002-run-001 \
  --lock /private/tmp/ccbench-runs/002-run-001.lock.json \
  --out /private/tmp/ccbench-runs/002-run-001.sealed
```

Run the hidden verifier. `--image` is optional when the case manifest declares
the correct verifier runtime image:

```bash
ccbench mvp evaluate \
  --case 002 \
  --submission /private/tmp/ccbench-runs/002-run-001.sealed \
  --logs /private/tmp/ccbench-runs/002-run-001.verifier-logs
```

## Compute routes

GPU calculations run directly inside the supplied CompShare instance and reuse
the qualified runtime image. Claude Code does not receive the CompShare account
key and does not create or delete instances. The maintainer must verify zero
owned instances after collecting the run.

For IKKEM CPU work, Claude Code writes a request under `compute-requests/`.
The request is data, not a shell or cloud credential channel:

```json
{
  "schema_version": "1.0",
  "compute_class": "cpu",
  "command": ["python", "work/run_cpu.py"],
  "resources": {
    "nodes": 1,
    "ntasks": 8,
    "cpus_per_task": 2,
    "memory_gb_per_node": 32,
    "walltime_min": 60,
    "gpus": 0
  },
  "inputs": [
    {"path": "work/run_cpu.py", "sha256": "<64 lowercase hex characters>"}
  ],
  "outputs": ["compute-results/cpu/result.json"]
}
```

The maintainer validates it before passing the pinned resources and paths to
the operator-side `ikkem-slurm` skill:

```bash
ccbench mvp compute-check \
  --bundle /private/tmp/ccbench-runs/002-run-001 \
  --request /private/tmp/ccbench-runs/002-run-001/compute-requests/cpu.json
```

The skill and SSH key stay outside the Candidate workspace. The maintainer
copies back only the declared outputs into `compute-results/`. Scheduler
`COMPLETED` is not a scientific verdict; Claude Code must inspect the output and
place its scientific submission under `final/`.

## MVP completion gate

A run is `MVP_EVALUATED` only when:

1. the operator lock verifies every immutable public byte;
2. `final/` passes quarantine and has a sealed manifest;
3. the hidden verifier emits a valid result;
4. any CPU handoff has a validated request and declared result set; and
5. the maintainer confirms no CompShare instance remains billable.

Formal autonomous qualification, automatic cloud provisioning, and generalized
multi-backend scheduling are intentionally outside this MVP path.
