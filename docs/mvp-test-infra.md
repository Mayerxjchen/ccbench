# Run a paper case

Install once in the infra checkout:

```bash
uv sync --frozen --extra dev --extra hpc
uv tool install --editable .
```

Keep credentials in the host environment or a local `.env`. Use `ANTHROPIC_BASE_URL` and the provider's `ANTHROPIC_AUTH_TOKEN` (Bearer) or `ANTHROPIC_API_KEY` (x-api-key). Fill the secret-free [candidate config](../examples/candidate-config.toml) with the model, images, budget and compute profile. The gateway requires an Anthropic Messages-compatible upstream.

A paper with a thin wrapper can run:

```bash
cd /path/to/paper-suite
uv run python eval.py 001
```

From the infra checkout, pass an external case path:

```bash
uv run python eval.py /path/to/paper-suite/cases/001-example --config /path/to/config/bench.toml
```

The common CLI is `bench run CASE --config CONFIG`. It creates a new run and starts Candidate Docker; a completed local submission is evaluated in a separate Verifier container.

For HPC, Candidate writes a validated compute request and stops with `COMPUTE_REQUIRED`. The trusted operator uses the selected Local/Slurm/CompShare profile to execute it, then imports results and resumes:

```bash
bench pilot import-results /path/to/run /path/to/operator-output
bench pilot resume /path/to/run
bench pilot evaluate /path/to/run
```

Formal import additionally requires the signed operator receipt. Formal startup requires the case-specific admission and qualified runtime identities; `--formal` alone does not create them. Operator tools under `scripts/infra/` create signing keys, qualify runtime images and create admission. Store private signing keys outside Candidate and public case material.

The run directory contains `workspace/`, `messages.jsonl`, `transcript.jsonl`, `run-state.json` and `effective-limits.json`, plus verification records when evaluation has run. Messages may be empty when the model failed before producing any; a failed run must not be presented as a completed scientific result.

The example exploration ceilings are 1024 gateway model requests (including retries), 5 million cumulative tokens and 14400 seconds cumulative Candidate phase walltime. External operator/Slurm waiting is excluded; compute job walltime is configured separately. `max_budget_usd = 0.0` disables the bridge's dollar estimate gate. Token accounting uses reported gateway usage and can overshoot by an in-flight response.

Freeze per-case model, skill, runtime and resource conditions before comparing agents. Missing scientific references or unqualified site software remain case-readiness blockers. Infra tests and signed records do not establish a scientific result.

See [external suite contract](external-suite.md) and [Slurm configuration](user/slurm_quickstart.md).
