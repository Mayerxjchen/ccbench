# Bench MVP test infrastructure

This is the deliberately small workflow for case development. Claude Code runs
inside a fresh Candidate Docker container attached only to a run-scoped
internal network. Hidden material is mounted only after the submission is
sealed.

The active runner is `container_claude_code`. The default Candidate and gateway
images come from `infra/config/agent-profiles.toml`; local users may select a
qualified runtime image without changing a case:

## Acceptance panel (2026-09-10)

| Gate | Evidence | Status |
|---|---|---|
| Repository regression | `1674 passed, 3 skipped`; diff, Python compile, and verifier shell syntax clean | PASS |
| Candidate Base | Claude Code 2.1.266, Python 3.11, NumPy/ASE and real Debian Packmol generation; arm64 image 416 MB | PASS |
| Candidate isolation | UID 10001, read-only root, internal-only run network, host-only credential proxy, no Docker socket | PASS |
| Candidate tool execution | mock Anthropic SSE runs Bash, returns tool_result, writes the exact-LF `final/` artifact, and exits cleanly | PASS |
| MatClaw verifier | real image as UID 65532, `--network none`, 512 MiB bounded tmpfs; Case 002 positive 9/9 and counted negative classification verified | PASS |
| Signed Formal mechanics | admission → Candidate → seal → separate verifier → bound Ed25519 receipt; stale image/key/preflight failures fail closed | PASS |
| Nonelinear authentication | standard `ANTHROPIC_AUTH_TOKEN` now uses one `Authorization: Bearer` header (never a simultaneous `x-api-key` header); the former HTTP 403 is resolved | PASS |
| Nonelinear upstream health | bearer-only probes currently reach the provider but time out or return HTTP 502; live model qualification remains external-service/account dependent | BLOCKED externally |

Cases 004 and 005 are retained for compatibility but are excluded from this
infra acceptance round.

A release is publication-ready only when every treatment/case row it claims is
PASS. A blocked optional model or absent case runtime does not invalidate the
passing MatClaw infrastructure, but it must not be included in a published
matrix. Re-run the panel after changing an agent profile, image, verifier
bundle, resource policy, or gateway route.

```bash
BENCH_CANDIDATE_IMAGE=my-case-runtime@sha256:<digest> \
BENCH_SIDECAR_IMAGE=my-gateway@sha256:<digest> \
bench pilot start 001-matclaw-cips-active-distillation
```

This image override example is for `LOCAL_DEV` only. Formal runs use the
coordinator-admitted profile and its qualified immutable Candidate/sidecar
images; loose `BENCH_*_IMAGE` overrides are rejected.

## 新增 case 的最小流程

新案例沿用 Bench 的 `case.toml`、`task.md`、`input/` 契约，并以一个
私有 `tests/` 目录承载 verifier launcher/tests；legacy case 的
`verifier/` 目录仍受支持。可通过 scaffold 自动生成这些边界：

```bash
bench case design --ir /path/to/case.ir.yaml --run-dir /tmp/bench-case
bench case build --run-dir /tmp/bench-case
```

生成的 draft 会同时包含 `environment/`（任务 Dockerfile/runtime）、
`solution/`（私有参考解）和 `tests/`（私有 verifier launcher/tests，作为
后续 worker 的完整 bundle）。
将它们替换为真实内容后运行 `bench case validate`、
`bench case release-check`。Candidate 导出仍只读取 `case.toml` 的
显式 `[candidate].files`；默认仅有 `input/**`，并始终额外映射任务说明为
`instruction.md`，不会复制上述私有目录。

可直接查看 [examples/case-template/README.md](../examples/case-template/README.md)
获取目录边界和迁移注意事项。现有五个 case 无需搬迁即可继续运行。

`ANTHROPIC_BASE_URL`, either `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`, and
`BENCH_MODEL` are read from the shell or repository `.env` (shell values win).
Use `ANTHROPIC_AUTH_TOKEN` for a Bearer-token provider such as Nonelinear and do
not set both credential variables. `ANTHROPIC_API_KEY` selects `x-api-key`
authentication for providers that require it.
The legacy `BENCH_BASE_URL`/`BENCH_API_KEY` pair remains accepted by local
smoke commands. The credential is held by the host
ModelGatewayProxy and is never passed to the Candidate. `BENCH_MODEL` is
opaque routing data and is forwarded unchanged; the proxy rejects a request
whose body model differs from the run-scoped value.

For repeatable user setup, the same values may be kept in the secret-free
`examples/candidate-config.toml` shape and passed with one flag:

```bash
bench pilot start 001-matclaw-cips-active-distillation \
  --config /path/to/candidate-config.toml
```

The config contains only model IDs, image names, environment-variable names,
and an operator compute-profile path. It must never contain an API key or
token. Explicit `BENCH_*` environment values override the file, which in
turn overrides the trusted agent profile. For a custom gateway model such as
`gemini-3.8-flash`, the requested/upstream ID stays exact while Claude Code
uses the pinned `claude-sonnet-4-6` client ID; the host proxy then
rewrites only the upstream body field and the run record stores both IDs.

### Exploration limits

The optional `[limits]` table is the single per-run budget override. It accepts
only `max_turns` (every gateway model request, including retries),
`max_total_tokens` (cumulative across Candidate resume attempts),
`agent_timeout_sec` (cumulative Candidate phase walltime, including setup and
cleanup; external HPC/operator waiting is not charged), and `max_budget_usd`.
The first three must be positive; the
last may be `0.0`, which disables the unreliable Claude-bridge dollar
estimator but does not disable turn, token, or walltime limits. Effective
values are written before execution to `effective-limits.json` and bound into
`run-state.json`; resume fails closed if they drift. The go-water
`config/bench.toml` and the example config use the deliberately generous
exploration values `1024 / 5,000,000 / 14,400s / $0.0`.
When a ceiling is reached, telemetry records a distinct budget-exceeded reason
and the run is classified as `AGENT_BUDGET_EXHAUSTED` (or `AGENT_TIMEOUT`),
not as a scientific verifier failure.

## Trust boundary

- The maintainer repository contains cases, verifiers, solutions, and references.
- `bench mvp export` creates a new workspace outside the repository from the
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
cd /path/to/bench
source .venv/bin/activate
```

From the MVP runner worktree during development, export a case:

```bash
bench mvp export \
  --case 002 \
  --out /private/tmp/bench-runs/002-run-001
```

This also creates the operator-only lock:

```text
/private/tmp/bench-runs/002-run-001.lock.json
```

Upload only `002-run-001/` to the clean CompShare instance. Start Claude Code
inside that directory. Do not upload the lock, repository, verifier, solution,
reference, or cloud account configuration.

After retrieving the workspace, verify it and freeze `final/`:

```bash
bench mvp check \
  --bundle /private/tmp/bench-runs/002-run-001 \
  --lock /private/tmp/bench-runs/002-run-001.lock.json

bench mvp freeze \
  --bundle /private/tmp/bench-runs/002-run-001 \
  --lock /private/tmp/bench-runs/002-run-001.lock.json \
  --out /private/tmp/bench-runs/002-run-001.sealed
```

Run the hidden verifier. `--image` is optional when the case manifest declares
the correct verifier runtime image:

```bash
bench mvp evaluate \
  --case 002 \
  --submission /private/tmp/bench-runs/002-run-001.sealed \
  --logs /private/tmp/bench-runs/002-run-001.verifier-logs
```

## Compute routes

The Candidate control layer is identical for all routes. A case may use
`local` execution in its selected runtime image, or emit a validated compute
request for an operator-selected CPU HPC (IKKEM) or GPU HPC (CompShare)
profile. Backend credentials and scheduler clients remain outside the
Candidate; the existing compute dispatcher owns that handoff.

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
bench mvp compute-check \
  --bundle /private/tmp/bench-runs/002-run-001 \
  --request /private/tmp/bench-runs/002-run-001/compute-requests/cpu.json
```

The skill and SSH key stay outside the Candidate workspace. The maintainer
copies back only the declared outputs into `compute-results/`. For
`EXTERNAL_SUBMISSION` and `FORMAL`, the operator also returns an Ed25519-signed
operator receipt binding the run id, attempt, backend, request digests, and
every output path/size/SHA-256. Configure the pinned public key and import it
explicitly:

```bash
export BENCH_OPERATOR_RECEIPT_TRUSTED_PUBLIC_KEY=<operator-public-key-hex>
export BENCH_OPERATOR_RECEIPT_KEY_ID=operator-v1
bench pilot import-results /private/tmp/bench-runs/002-run-001 \
  /private/tmp/operator-results --receipt /private/tmp/operator-receipt.json
```

The pilot verifies the signature before copying, stores a controlled receipt
copy outside the Candidate mount, and rechecks its output manifest on resume
and evaluate. `LOCAL_DEV` may omit a receipt and is never publication eligible.
Scheduler
`COMPLETED` is not a scientific verdict; Claude Code must inspect the output and
place its scientific submission under `final/`.

## MVP completion gate

A run is `MVP_EVALUATED` only when:

1. the operator lock verifies every immutable public byte;
2. `final/` passes quarantine and has a sealed manifest;
3. the hidden verifier emits a valid result;
4. any CPU handoff has a validated request and declared result set; and
5. the maintainer confirms no CompShare instance remains billable.

Formal autonomous qualification and automatic cloud provisioning remain
coordinator/operator responsibilities. The supported compute boundary is
explicit: local execution, operator-selected IKKEM CPU, or operator-selected
CompShare GPU; these routes share the Candidate Docker control layer.

## Trust modes and the two-stage verifier

Pilot records one of three trust levels: `LOCAL_DEV`,
`EXTERNAL_SUBMISSION`, or `FORMAL`. A local run (including a same-host
`pilot evaluate`) remains `LOCAL_DEV` and is never a formal score.
`formal` is an admission decision made by the trusted coordinator; it is not a
user TOML switch. User configuration may select `local`/`untrusted` or
`external-verifier`/`external_submission`, but cannot promote itself.

The handoff is deliberately two-stage:

1. the Agent receives only the exported public workspace and writes a final
   artifact;
2. the coordinator seals the allowlisted artifact, then starts the separate
   verifier worker with `--network none`, `--read-only`, no Docker socket, and
   only `/submission:ro` plus the case's private verifier bundle.

The worker emits `verifier-receipt.json`, binding the case, verifier profile
and digest, verifier image digest, sealed manifest digest, and result. Every
case may share the verifier runner/base image, but its private bundle and
 qualified profile digest remain case-specific. The Agent never receives the
 private bundle, verifier image, signing key, or scheduler credentials.

### Reproducible local qualification

Build the reusable images from the pinned recipes, then qualify the exact
images on the same Docker host. Qualification records the inspected local
Image ID (never a guessed digest) and signs one receipt per image:

```bash
cd /path/to/bench
./runtimes/recipes/claude-code-candidate-base/build.sh \
  --base-image-digest sha256:<node-22-digest> --claude-code-version <exact-version>
./runtimes/recipes/model-gateway-sidecar/build.sh \
  --base-image-digest sha256:<python-digest>
BENCH_BASE_IMAGE=ubuntu:24.04@sha256:<verified-ubuntu-digest> \
BENCH_UV_IMAGE=ghcr.io/astral-sh/uv:0.8.14@sha256:<verified-uv-digest> \
BENCH_PYTHON_IMAGE=python:3.11.15-slim-bookworm@sha256:<verified-python-digest> \
  bash runtimes/recipes/build.sh matclaw-cips
.venv/bin/python scripts/infra/qualify_candidate_runtimes.py \
  --candidate-image bench-agent-claude-code:2.1.266 \
  --sidecar-image bench-gateway-anthropic:1 \
  --signing-key-hex "$BENCH_IMAGE_QUALIFICATION_SIGNING_KEY"
```

Set `BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY` and the matching
`BENCH_IMAGE_QUALIFICATION_KEY_ID` before a local Formal run. The profile
loads the generated locks from `~/.config/bench/qualifications/` and checks
the live Image ID again immediately before `docker run`. A registry-backed
publication additionally requires a qualified `repo@sha256:...` lock and the
coordinator's trusted receipt; a local Image ID is intentionally host-bound.

The three case verifier runtimes use the same pattern. Build each scientific
runtime recipe, then materialize locks in the same directory. The command
executes the public verifier launcher plus a smoke test using the image-owned
Python as UID 65532:

```bash
.venv/bin/python scripts/infra/qualify_verifier_runtimes.py \
  --output-dir ~/.config/bench/verifier-qualifications \
  --signing-key-hex "$BENCH_IMAGE_QUALIFICATION_SIGNING_KEY" \
  --profile-image matclaw-cips-v1=bench-runtime-deepmd-kit:2.2.11-cpu \
  --profile-image ai2kit-v1=ai2kit-runtime-v1 \
  --profile-image dpmp-v1=dftworld-deepmd-jax:0.1.0-cpu
export BENCH_VERIFIER_QUALIFICATION_DIR="$HOME/.config/bench/verifier-qualifications"
export BENCH_VERIFIER_QUALIFICATION_PUBLIC_KEY="$BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY"
```

Repository legacy locks remain unchanged until this command is run against
real rebuilt images; Formal never upgrades a `BUILT_NOT_QUALIFIED` lock.

### Trust-key onboarding and Formal run

Local users do not need any signing keys. They may run Candidate Docker and the
verifier as `LOCAL_DEV`; those results are explicitly non-publication and
cannot be promoted. Formal keys belong to the coordinator/operator, never to
the Candidate container. Generate each role in a protected directory (the
script prints paths only, never private key material):

```bash
.venv/bin/python scripts/infra/generate_signing_keys.py \
  --output-dir /secure/bench-keys --role image-qualification
.venv/bin/python scripts/infra/generate_signing_keys.py \
  --output-dir /secure/bench-keys --role formal
.venv/bin/python scripts/infra/generate_signing_keys.py \
  --output-dir /secure/bench-keys --role verifier
```

The private files are 0600 and are loaded only by the corresponding
coordinator command. The public trust anchors and key IDs are supplied through
these exact environment variables (values are never recorded in run state):

```bash
export BENCH_IMAGE_QUALIFICATION_SIGNING_KEY="$(cat /secure/bench-keys/image-qualification.private.hex)"
export BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY="$(cat /secure/bench-keys/image-qualification.public.hex)"
export BENCH_IMAGE_QUALIFICATION_KEY_ID=image-qualification-v1
export BENCH_FORMAL_SIGNING_KEY="$(cat /secure/bench-keys/formal.private.hex)"
export BENCH_FORMAL_TRUSTED_PUBLIC_KEY="$(cat /secure/bench-keys/formal.public.hex)"
export BENCH_FORMAL_KEY_ID=coordinator-v1
export BENCH_VERIFIER_SIGNING_KEY="$(cat /secure/bench-keys/verifier.private.hex)"
export BENCH_VERIFIER_TRUSTED_PUBLIC_KEY="$(cat /secure/bench-keys/verifier.public.hex)"
export BENCH_VERIFIER_KEY_ID=verifier-coordinator-v1
export BENCH_VERIFIER_QUALIFICATION_PUBLIC_KEY="$BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY"
```

The complete coordinator flow is qualification, admission, Candidate start,
then evaluation. `RUN_ID` must be the final run directory name; the admission
script derives the public digest and the selected registered profile from the
case and signed qualification locks:

```bash
RUN_ID=case002-formal-001
RUN=/secure/bench-runs/$RUN_ID
.venv/bin/python scripts/infra/make_formal_admission.py \
  --case 002 --agent-profile claude-mvp --run-id "$RUN_ID" \
  --output /secure/bench-admissions/$RUN_ID.json
bench pilot start 002 --out "$RUN" --formal \
  --agent-profile claude-mvp \
  --formal-admission /secure/bench-admissions/$RUN_ID.json
bench pilot evaluate "$RUN"
```

The `gemini-3.8-flash` example is a Local treatment only until its clean
termination, tool-use, and verifier qualification evidence has passed; it is
not registered as a Formal profile by default.
