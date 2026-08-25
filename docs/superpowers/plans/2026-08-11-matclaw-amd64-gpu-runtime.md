# MatClaw amd64 GPU Runtime Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and enforce one qualified `linux/amd64` GPU Apptainer runtime for Cases 031–033, then unblock diagnostic and formal paper execution on the A100 cluster without scoring HPC mechanics as Agent capability.

**Architecture:** Build pinned CPU-amd64 and GPU-amd64 OCI images locally with Buildx, transfer the exact GPU Docker archive to the cluster, convert it to a hash-locked SIF, and qualify it through the restricted controller and Slurm/A100 path. A shared runtime lock and matching qualification receipt gate every scientific submission; the Slurm prologue repeats the checks to prevent remote replacement.

**Tech Stack:** Docker Buildx/QEMU, Apptainer 1.4.0, Slurm, NVIDIA A100-SXM4-80GB, TensorFlow 2.16.2, DeePMD-kit 2.2.11, Python 3.11, Bash, JSON, pytest.

**Plan integration:** This is the fail-closed runtime sub-plan for Task 6 of
`docs/superpowers/plans/2026-08-11-matclaw-three-case-success.md`. Complete it
before that plan's Tasks 11–13. Scientific repairs and acceptance remain owned
by Tasks 7–9 of the three-case plan: 031 active distillation, 032 resumable
13-temperature Curie workflow, and 033 history-dependent adaptive search.

## Global Constraints

- Target runtime is exactly `linux/amd64`; compute `uname -m` is `x86_64`.
- Formal runtime is GPU-amd64; CPU-amd64 is diagnostic-only.
- Keep controller operations limited to `stage/submit/status/log/cancel/fetch`.
- SSH/Slurm/Apptainer failures are infrastructure failures and never evaluated-Agent failures.
- Do not overwrite failed diagnostics or silently rewrite the arm64 lock as valid.
- Formal work uses one immutable GPU SIF hash and one source commit.
- Cases 031–033 test scientific implementation, not HPC Skill usage.

---

### Task 1: Make Runtime Locks Fail Closed

**Files:**
- Create: `scripts/matclaw_runtime_lock.py`
- Create: `tests/test_matclaw_runtime_lock.py`
- Modify: `evidence/matclaw/formal/031-runtime-lock.json`
- Generate later: `evidence/matclaw/formal/runtime-gpu-amd64.lock.json`

**Interfaces:**
- `load_runtime_lock(path: Path) -> dict`
- `validate_runtime_lock(lock: dict, require_formal: bool) -> list[str]`
- `validate_qualification(lock: dict, receipt: dict) -> list[str]`
- Valid architecture tuple: `("linux", "amd64", "x86_64")`.

- [ ] Add failing tests that reject the current arm64/CPU lock, an image ID mislabelled as an OCI repository digest, missing archive/SIF hashes, `formal_eligible=false`, stale receipts, receipt/SIF mismatch, CPU runtime type, and non-x86_64 compute architecture. Accept one complete synthetic GPU-amd64 lock and matching receipt.
- [ ] Run `uv run pytest -q tests/test_matclaw_runtime_lock.py`; expect failure because the validator does not exist.
- [ ] Implement schema version 2. Required identity fields are `runtime_type=gpu`, `os=linux`, `architecture=amd64`, `compute_uname_m=x86_64`, source commit, CPU/GPU image IDs, optional real OCI repository digest, Docker archive SHA, SIF path/SHA, qualification path/SHA, and `formal_eligible`.
- [ ] Allow `oci_repo_digest=null` for an unpushed image; never accept a Docker image ID in that field. Require the receipt to repeat the SIF SHA, A100 identity, architecture, versions, parity, frame count, and `qualified=true`.
- [ ] Preserve the old lock identities and add `formal_eligible=false`, `runtime_type=cpu`, `architecture=arm64`, `invalid_reason=architecture_mismatch`, and `superseded_by=runtime-gpu-amd64.lock.json`.
- [ ] Run focused tests and `git diff --check`, then commit:

```bash
git add scripts/matclaw_runtime_lock.py tests/test_matclaw_runtime_lock.py evidence/matclaw/formal/031-runtime-lock.json
git commit -m "fix: reject ineligible MatClaw runtime locks"
```

### Task 2: Build CPU-amd64 and GPU-amd64 Artifacts

**Files:**
- Create: `scripts/build_matclaw_amd64_gpu.sh`
- Create: `tests/test_matclaw_amd64_build_contract.py`
- Modify: `base-env-build/matclaw-cips-gpu/Dockerfile`
- Modify: `docs/gpu-execution.md`
- Generated outside Git: `artifacts/matclaw-cips-2.2.11-gpu-amd64.tar`
- Generated outside Git: `artifacts/matclaw-cips-2.2.11-gpu-amd64.build.json`

**Interfaces:**
- CPU tag: `dftworld-base-matclaw-cips:2.2.11-cpu-amd64`.
- GPU tag: `dftworld-base-matclaw-cips:2.2.11-gpu-amd64`.
- Platform: `linux/amd64`.

- [ ] Add tests requiring two explicit `docker buildx build --platform linux/amd64 --load` calls, the CPU-amd64 `BASE_IMAGE`, GPU-only archive export, Docker `linux/amd64` inspection, provenance hashes, and no retagging of CPU/arm64 images.
- [ ] Run `uv run pytest -q tests/test_matclaw_amd64_build_contract.py`; expect failure because the script is absent.
- [ ] Implement the equivalent fixed commands:

```bash
docker buildx build --platform linux/amd64 --load \
  -t dftworld-base-matclaw-cips:2.2.11-cpu-amd64 \
  -f base-env-build/matclaw-cips/Dockerfile .
docker buildx build --platform linux/amd64 --load \
  --build-arg BASE_IMAGE=dftworld-base-matclaw-cips:2.2.11-cpu-amd64 \
  -t dftworld-base-matclaw-cips:2.2.11-gpu-amd64 \
  -f base-env-build/matclaw-cips-gpu/Dockerfile .
docker image inspect --format '{{.Os}}/{{.Architecture}}' \
  dftworld-base-matclaw-cips:2.2.11-gpu-amd64
docker save --output artifacts/matclaw-cips-2.2.11-gpu-amd64.tar \
  dftworld-base-matclaw-cips:2.2.11-gpu-amd64
```

- [ ] Fail unless inspection is exactly `linux/amd64`. Emit image IDs, optional repository digests, archive SHA, Git commit/dirty flag, Docker/Buildx versions, Dockerfile hash, qualification-script hash, and resolved `/opt/matclaw` package freeze.
- [ ] Run `bash scripts/build_matclaw_amd64_gpu.sh`. QEMU failure is an infrastructure build failure; do not bypass the scientific smoke gate.
- [ ] Run focused tests and `bash -n`, then commit tracked files:

```bash
git add scripts/build_matclaw_amd64_gpu.sh tests/test_matclaw_amd64_build_contract.py base-env-build/matclaw-cips-gpu/Dockerfile docs/gpu-execution.md
git commit -m "feat: build pinned MatClaw GPU amd64 artifacts"
```

### Task 3: Convert the Exact GPU Archive to a SIF

**Files:**
- Create: `scripts/hpc/build_matclaw_gpu_sif.sh`
- Create: `tests/test_matclaw_sif_build_contract.py`
- Generate: `evidence/matclaw/qualification/sif-build-amd64.json`

**Interfaces:**
- Remote root: `/public/home/<site-user>/dftworld2-runs/matclaw-runtime/`.
- Archive: `matclaw-cips-2.2.11-gpu-amd64.tar`.
- SIF: `matclaw-cips-2.2.11-gpu-amd64.sif`.
- Apptainer: `/public/software/apptainer/bin/apptainer` version 1.4.0.

- [ ] Add tests requiring checksum-aware rsync, archive verification before build, a `gpu-amd64` SIF filename, `docker-archive` input, post-build SHA, `apptainer inspect`, and `apptainer exec ... uname -m` requiring x86_64.
- [ ] Implement staging with `rsync -av --checksum` and a fixed remote script. Fail on an existing mismatched SIF instead of overwriting it.
- [ ] Build with:

```bash
/public/software/apptainer/bin/apptainer build \
  /public/home/<site-user>/dftworld2-runs/matclaw-runtime/matclaw-cips-2.2.11-gpu-amd64.sif \
  docker-archive:///public/home/<site-user>/dftworld2-runs/matclaw-runtime/matclaw-cips-2.2.11-gpu-amd64.tar
```

- [ ] Record SIF SHA, archive SHA, inspection output, Apptainer version, builder hostname/architecture, and require `apptainer exec <SIF> uname -m` to print `x86_64`.
- [ ] Run focused tests and `bash -n`, then commit:

```bash
git add scripts/hpc/build_matclaw_gpu_sif.sh tests/test_matclaw_sif_build_contract.py
git commit -m "feat: build hash-locked MatClaw GPU SIF"
```

### Task 4: Qualify the SIF on A100 and Issue the Shared Lock

**Files:**
- Create: `scripts/hpc/matclaw_gpu_qualify.slurm`
- Modify: `base-env-build/matclaw-cips-gpu/qualify_gpu.py`
- Modify: `scripts/matclaw_hpc_controller.py`
- Modify: `tests/test_matclaw_hpc_controller.py`
- Generate: `evidence/matclaw/qualification/a100-gpu-amd64.json`
- Generate: `evidence/matclaw/formal/runtime-gpu-amd64.lock.json`

**Interfaces:**
- Use existing fixed `probe` run kind and six controller operations.
- Required GPU: `NVIDIA A100-SXM4-80GB`.
- Energy and maximum force-component parity differences: each `<1e-6`.
- MD: 100 steps and exactly 101 finite frames.

- [ ] Add tests that allow `probe` before qualification but make `paper/smoke` reject missing, CPU, arm64, stale, unqualified, wrong-SIF, and wrong-cluster receipts. A matching receipt unlocks scientific submission.
- [ ] Create a qualification Slurm job with pinned partition `gpu`, account `acct-blocked`, QoS `normal`, one GPU, eight CPUs, 64 GB, and an eight-hour limit. Before Python, verify SIF SHA, `uname -m=x86_64`, and A100 visibility through `apptainer exec --nv`.
- [ ] Run `/opt/matclaw/qualify_gpu.py` against the locked structure/model. Receipt fields include Slurm job/node, driver, GPU, TensorFlow/DeePMD versions, parity, frames, wall time, SIF SHA, and `qualified`.
- [ ] Submit through `python scripts/matclaw_hpc_controller.py` using only
  `stage`, `submit --run-kind probe`, `status`, and `fetch`. Require Slurm
  `COMPLETED`, A100 identity, `qualified=true`, and matching SIF SHA.
- [ ] Generate `runtime-gpu-amd64.lock.json` from build JSON, SIF-build JSON, and receipt; never hand-edit `formal_eligible`.
- [ ] Validate and commit:

```bash
uv run python scripts/matclaw_runtime_lock.py validate evidence/matclaw/formal/runtime-gpu-amd64.lock.json --require-formal
uv run pytest -q tests/test_matclaw_runtime_lock.py tests/test_matclaw_hpc_controller.py
git add scripts/hpc/matclaw_gpu_qualify.slurm base-env-build/matclaw-cips-gpu/qualify_gpu.py scripts/matclaw_hpc_controller.py tests/test_matclaw_hpc_controller.py evidence/matclaw/qualification/a100-gpu-amd64.json evidence/matclaw/formal/runtime-gpu-amd64.lock.json
git commit -m "feat: qualify and lock MatClaw A100 runtime"
```

### Task 5: Generalize the Transparent Controller to 031–033

**Files:**
- Modify: `scripts/matclaw_hpc_controller.py`
- Modify: `scripts/hpc/031_matclaw_gpu.slurm`
- Create: `scripts/hpc/matclaw_gpu_paper.slurm`
- Modify: `tests/test_matclaw_hpc_controller.py`
- Modify: `eval.py`

**Interfaces:**
- Allow-list exactly Cases 031, 032, and 033.
- Confined roots: `/public/home/<site-user>/dftworld2-runs/matclaw-031/`, `matclaw-032/`, and `matclaw-033/`.
- Environment allow-list remains `MATCLAW_PROFILE`, `MATCLAW_SEED`, and `MATCLAW_OUTPUT`.
- Terminal classifications: `infrastructure_failed`, `scientific_failed`, `completed`, `cancelled`.

- [ ] Add tests for all three valid case policies, cross-case/root rejection, unknown cases, fixed resources, no arbitrary shell/env, runtime receipt gating, and terminal classification. Infrastructure failure must not call a verifier or create a reward.
- [ ] Replace the 031-only prefix/root logic with a constant case policy mapping. Caller values cannot define roots, mounts, Slurm directives, or commands.
- [ ] Keep six public operations. `submit` internally validates the shared lock and fetched receipt before `paper/smoke`.
- [ ] Add this fixed in-job prologue before `/solution/solve.sh`:

```bash
test "$(sha256sum "$MATCLAW_SIF" | awk '{print $1}')" = "$EXPECTED_SIF_SHA256"
test "$("$APPTAINER" exec "$MATCLAW_SIF" uname -m)" = x86_64
"$APPTAINER" exec --nv "$MATCLAW_SIF" /opt/matclaw/bin/python -c \
  'import tensorflow as tf; assert tf.config.list_physical_devices("GPU")'
```

- [ ] Failure before solution start writes `infrastructure_failed` and exits without verification. Failure after complete scientific artifacts is independently classified by the hidden verifier.
- [ ] Run controller tests, shell syntax, and one fixed canary for each case policy. Each canary must fetch a hash-verified log with the same A100 and SIF SHA.
- [ ] Commit:

```bash
git add scripts/matclaw_hpc_controller.py scripts/hpc/031_matclaw_gpu.slurm scripts/hpc/matclaw_gpu_paper.slurm tests/test_matclaw_hpc_controller.py eval.py
git commit -m "feat: gate MatClaw 031-033 on qualified GPU runtime"
```

### Task 6: Run the Diagnostic Paper Gate

**Files:**
- Modify: `evidence/matclaw/diagnostic/index.json`
- Modify: `evidence/matclaw/diagnostic/summary.md`
- Generated outside Git: diagnostic raw artifact bundle.

**Interfaces:**
- Seed: `2026081199`.
- Runtime lock: `evidence/matclaw/formal/runtime-gpu-amd64.lock.json`.
- Evidence class: `diagnostic`.

- [ ] Record source commit, scoped-clean assertion, public/solution hashes, runtime-lock/SIF hashes, seed, case, profile, and isolated paths. Keep the CPU 032 resume run diagnostic-only.
- [ ] Enforce dependency order: GPU-node probe `COMPLETED` -> runtime qualification `qualified=true` -> diagnostic paper job. GPU-node visibility alone is insufficient.
- [ ] Classify pre-solution runtime/Slurm/checksum failure as `infrastructure_failed`; complete artifacts rejected by science as `scientific_failed`; accepted diagnostics as `completed`. No diagnostic sets `benchmark_valid=true`.
- [ ] Review frame counts, metrics, restart records, GPU utilization/timing, verifier recomputation, and hashes. Each rerun gets a new diagnostic ID; preserve prior failures.
- [ ] Run the pre-formal gate:

```bash
uv run pytest -q tests
git diff --check
uv run python scripts/matclaw_runtime_lock.py validate evidence/matclaw/formal/runtime-gpu-amd64.lock.json --require-formal
```

Expected: tests pass, scoped diff is clean, runtime lock validates, diagnostic review is recorded, and no formal run began before these gates.

## Completion Boundary

This P0 plan completes when the shared GPU-amd64 runtime is qualified and
locked, the controller gates all three cases on that runtime, and one paper
diagnostic is correctly classified. Then resume Tasks 7–13 of the three-case
success plan: close any remaining scientific repair, freeze the exact source
commit and SIF hash, run two independent formal reproductions for each case,
and promote a case only when its single-run and cross-run gates both pass.
