# MatClaw Case 031 Reconstruction Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair Case 031 so two independently verified GPU paper runs demonstrate a real active-distillation iteration and reproduce the source force MAE.

**Architecture:** Put deterministic selection and batch rules in a pure module, keep simulation/training in the reference orchestrator, persist hash-locked atomic checkpoints, and make the hidden verifier independently derive every scientific gate from raw artifacts. Separate API-driven Agent evaluation from the direct reference workflow.

**Tech Stack:** Python 3.11, pytest, NumPy, ASE, DeePMD-kit 2.2.11, TensorFlow 2.16.2, Docker/NVIDIA runtime, JSON, Bash.

## Global Constraints

- Keep the committee-deviation selection band exactly `[0.05, 0.15] eV/angstrom`.
- Never select a frame outside the band, duplicate a frame, fabricate a frame, or leak held-out configurations.
- Paper mode must complete at least one real selection-label-retrain iteration even when iteration-zero MAE is already below `0.10`.
- Preserve the 3x3x1 paper system, four initial teacher temperatures, pinned teacher/structure hashes, held-out set, two-member committee, and source-size `(240, 240, 240)` fitting network.
- Treat all pre-freeze runs as diagnostic and keep `benchmark_valid=false`.
- Formal runs require one clean commit, one immutable GPU image digest, distinct seeds, isolated empty workspaces, and no cross-run checkpoint/model/trajectory reuse.
- The Agent stays in the local Case 031 Docker sandbox; the controller and `SshSlurmTransport` run inside that container and expose only `stage/submit/status/log/cancel/fetch`.
- SSH authentication enters the local controller container through a forwarded agent socket plus read-only config/known-hosts mounts; private-key bytes remain outside the image and `/app`.
- The HPC target is SSH alias `<site-alias>`, Slurm partition `gpu`, one A100 GPU, Apptainer `1.4.0`, and a remote root below `/public/home/<site-user>/dftworld2-runs/matclaw-031/`.
- Docker daemon access is not required. Formal execution uses Apptainer `--nv` and records both the source OCI digest and SIF SHA-256.
- Single-run acceptance: final MAE `<0.10 eV/angstrom`, source-relative error from `0.098` `<=25%`, active iterations `>=1`, and hidden verifier valid.
- Cross-run acceptance: absolute MAE difference `<=0.01 eV/angstrom`.
- Do not modify Cases 032, 033, or unrelated repository files.
- Do not start a long calculation until its CPU tests, GPU qualification, and diagnostic prerequisites pass.

---

## File Map

- Create `031-matclaw-cips-active-distillation/solution/active_contract.py`: pure selection, batch, de-duplication, and growth rules.
- Create `031-matclaw-cips-active-distillation/tests/test_active_contract.py`: dependency-light TDD coverage.
- Modify `031-matclaw-cips-active-distillation/solution/run_distillation.py`: seedable, resumable active workflow.
- Modify `031-matclaw-cips-active-distillation/solution/run_profiles.json`: declared exploration batches and checkpoint contract.
- Modify `031-matclaw-cips-active-distillation/solution/solve.sh`: explicit seed/resume forwarding.
- Modify `031-matclaw-cips-active-distillation/solution/alt_distillation.py`: independent implementation of the same artifact contract.
- Modify `031-matclaw-cips-active-distillation/tests/verifier.py`: fail-closed raw-evidence checks.
- Modify `031-matclaw-cips-active-distillation/tests/test_outputs.py`: positive and forged submission tests.
- Modify `031-matclaw-cips-active-distillation/tests/test.sh`: run both pure and artifact tests.
- Modify `031-matclaw-cips-active-distillation/VALIDATION.json` and `benchmark_valid.json`: generated false state until two formal runs pass.
- Create `scripts/matclaw_hpc_controller.py`: restricted Case 031 adapter over the existing Case 034 `SshSlurmTransport`.
- Create `scripts/hpc/031_matclaw_gpu.slurm`: Apptainer/A100 batch entry point.
- Create `tests/test_matclaw_hpc_controller.py`: request validation, state mapping, path confinement, and checksum-sync tests.
- Create `base-env-build/matclaw-cips-controller/Dockerfile`: local Agent/controller image with OpenSSH client, rsync, and the restricted controller CLI but no hidden solution.
- Modify `eval.py`: for Case 031 controller mode, forward the SSH-agent socket and mount SSH config/known-hosts read-only without copying private keys.

### Task 1: Lock the Active-Selection Contract with Failing Tests

**Files:**
- Create: `031-matclaw-cips-active-distillation/tests/test_active_contract.py`
- Create: `031-matclaw-cips-active-distillation/solution/active_contract.py`

**Interfaces:**
- `select_informative(deviations: np.ndarray, hashes: list[str], excluded: set[str], low: float, high: float, cap: int) -> list[int]`
- `next_batch(batches: list[dict], completed: int) -> dict | None`
- `assert_exact_growth(before_hashes: list[str], selected_hashes: list[str], after_hashes: list[str]) -> None`

- [ ] **Step 1: Write RED tests for inclusive, unique selection**

```python
def test_selection_is_inclusive_unique_and_deterministic():
    indices = select_informative(
        np.array([0.049, 0.05, 0.10, 0.15, 0.151, 0.10]),
        ["a", "b", "c", "d", "e", "c"],
        excluded={"d"}, low=0.05, high=0.15, cap=8,
    )
    assert indices == [2, 1]

def test_empty_batch_advances_without_relaxing_band():
    batches = [{"temperature_K": 200}, {"temperature_K": 600}]
    assert next_batch(batches, 1) == {"temperature_K": 600}
    assert next_batch(batches, 2) is None

def test_dataset_growth_must_equal_unique_selection():
    with pytest.raises(ValueError, match="exactly equal"):
        assert_exact_growth(["a", "b"], ["c"], ["a", "b", "d"])
```

- [ ] **Step 2: Run and verify the intended RED failure**

Run:

```bash
docker run --rm -v "$PWD/031-matclaw-cips-active-distillation:/case" \
  dftworld-base-matclaw-cips:2.2.11-cpu \
  /opt/matclaw/bin/python -m pytest -q /case/tests/test_active_contract.py
```

Expected: collection fails because `solution.active_contract` does not exist.

- [ ] **Step 3: Implement the minimal pure contract**

Implement validation for equal deviation/hash lengths, finite deviations,
`0 <= low <= high`, and positive cap. Sort eligible indices by
`(-deviation, hash, original_index)`, exclude repeated/existing hashes, and
raise from `assert_exact_growth` unless `after == before + selected` exactly.

- [ ] **Step 4: Run GREEN tests**

Run the Task 1 Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/active_contract.py \
  031-matclaw-cips-active-distillation/tests/test_active_contract.py
git commit -m "test: lock Case 031 active selection contract"
```

### Task 2: Add Explicit Seed and Run-Identity Handling

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/run_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/solution/solve.sh`
- Modify: `031-matclaw-cips-active-distillation/tests/test_active_contract.py`

**Interfaces:**
- CLI adds `--seed INT` and `--resume`.
- `resolve_run_identity(profile_name: str, profile: dict, seed: int, structure_sha256: str, teacher_sha256: str) -> dict`.
- Environment contract: `MATCLAW_PROFILE`, `MATCLAW_SEED`, `MATCLAW_OUTPUT`, and optional `MATCLAW_RESUME=1`.

- [ ] **Step 1: Write RED tests**

Test that changing seed changes `run_identity_sha256`, identical inputs reproduce
it, absent `--seed` uses the profile seed only in smoke mode, and paper mode
requires an explicit seed.

- [ ] **Step 2: Verify RED**

Run the focused test file. Expected: failures for missing identity and CLI seed behavior.

- [ ] **Step 3: Implement identity and shell forwarding**

Canonicalize identity JSON with `sort_keys=True` and separators `(',', ':')`.
Update `solve.sh` to execute:

```bash
args=(--profile "${MATCLAW_PROFILE:?required}" --seed "${MATCLAW_SEED:?required}" \
      --output "${MATCLAW_OUTPUT:-/app}")
if [ "${MATCLAW_RESUME:-0}" = "1" ]; then args+=(--resume); fi
exec /opt/matclaw/bin/python "$SCRIPT_DIR/run_distillation.py" "${args[@]}"
```

- [ ] **Step 4: Run GREEN tests and shell syntax check**

Run focused tests and `bash -n 031-matclaw-cips-active-distillation/solution/solve.sh`.
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/run_distillation.py \
  031-matclaw-cips-active-distillation/solution/solve.sh \
  031-matclaw-cips-active-distillation/tests/test_active_contract.py
git commit -m "feat: make Case 031 run identity explicit"
```

### Task 3: Replace Single-Batch Abort with Bounded Physical Exploration

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/run_profiles.json`
- Modify: `031-matclaw-cips-active-distillation/solution/run_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test_active_contract.py`

**Interfaces:**
- Paper profile adds `selection_band_eV_A`, `min_active_iterations`, and `exploration_batches`.
- Each batch has `temperature_K`, `md_steps`, `driver_member`, and `seed_offset`.
- Each iteration record adds `exploration_attempts`, `deviation_summary`, and `selected_configuration_hashes`.

- [ ] **Step 1: Write RED controller tests**

Use a fake MD callback returning deviations outside the band for batch 1 and
inside the band for batch 2. Assert both batches run, the unchanged band is used,
and only batch-2 indices are selected. Add an exhaustion test requiring
`stop_reason=no_informative_configurations_after_all_declared_batches` and an
invalid/non-formal outcome.

- [ ] **Step 2: Verify RED**

Run the focused tests. Expected: current implementation stops after batch 1.

- [ ] **Step 3: Declare the paper batches**

Use the frozen order `200, 600, 1000, 350, 700, 250 K`. Alternate
`driver_member` 0 and 1; assign unique seed offsets. Keep `md_steps=5000` per
paper batch and the existing smoke-scale lengths for smoke tests.

- [ ] **Step 4: Implement cumulative selection**

After every batch, recompute deviations using both models, append the raw
trajectory record and quantiles `min/p25/median/p75/max`, call
`select_informative`, and continue only while selection is empty. Never modify
the band or select a nearest candidate.

- [ ] **Step 5: Run GREEN tests**

Expected: controller and pure contract tests pass.

- [ ] **Step 6: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/run_profiles.json \
  031-matclaw-cips-active-distillation/solution/run_distillation.py \
  031-matclaw-cips-active-distillation/tests/test_active_contract.py
git commit -m "fix: extend Case 031 exploration honestly"
```

### Task 4: Require a Genuine Selection-Label-Retrain Step

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/run_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test_active_contract.py`

**Interfaces:**
- `may_accept_convergence(mae: float, completed_active_iterations: int, minimum: int) -> bool`.
- History rows include `training_frames_before`, `selected_frames`, `training_frames_after`, and `teacher_label_sha256`.

- [ ] **Step 1: Write RED tests for iteration-zero convergence**

```python
def test_iteration_zero_cannot_satisfy_paper_active_workflow():
    assert not may_accept_convergence(0.079, completed_active_iterations=0, minimum=1)
    assert may_accept_convergence(0.099, completed_active_iterations=1, minimum=1)
```

Add a test that selected hashes are teacher-labelled, appended once, and the
next iteration's training hashes equal previous hashes plus selected hashes.

- [ ] **Step 2: Verify RED**

Expected: current iteration-zero early stop violates the test.

- [ ] **Step 3: Change terminal semantics**

Evaluate iteration-zero MAE for reporting, but enter exploration until
`min_active_iterations` is satisfied. After selection, label with the pinned
teacher, write the new dataset, train two new models, then evaluate convergence.
Only the retrained iteration may use `heldout_force_mae_below_0.10`.

- [ ] **Step 4: Verify GREEN and no data leakage**

Run focused tests. Expected: mandatory active step, exact growth, and disjoint
held-out hashes all pass.

- [ ] **Step 5: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/run_distillation.py \
  031-matclaw-cips-active-distillation/tests/test_active_contract.py
git commit -m "fix: require real Case 031 active retraining"
```

### Task 5: Add Hash-Locked Atomic Checkpoint and Resume

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/run_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test_active_contract.py`

**Interfaces:**
- `write_checkpoint(path: Path, state: dict) -> None`.
- `load_checkpoint(path: Path, expected_identity: dict) -> dict`.
- Checkpoint schema: `schema_version`, `run_identity`, `completed_stage`, `artifact_hashes`, `history`, `selected_hashes`.

- [ ] **Step 1: Write RED checkpoint tests**

Test atomic replacement, valid resume, wrong seed/profile/teacher rejection,
missing artifact rejection, hash mismatch rejection, and refusal to use a
nonempty output directory without `--resume`.

- [ ] **Step 2: Verify RED**

Expected: checkpoint interfaces are missing.

- [ ] **Step 3: Implement stage commits**

Write `checkpoint.json.tmp`, flush and `os.fsync`, then `Path.replace`. Commit a
checkpoint after teacher MD, held-out labelling, each committee member, each
exploration trajectory, each selected label set, and each retrained iteration.

- [ ] **Step 4: Implement strict resume**

Before reusing a stage, compare run identity and every recorded artifact hash.
On any mismatch, raise `RuntimeError` naming the first invalid artifact; do not
delete or silently regenerate it under `--resume`.

- [ ] **Step 5: Run GREEN tests and interruption simulation**

Expected: a synthetic interruption resumes from the last complete stage and
produces the same final history as uninterrupted execution.

- [ ] **Step 6: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/run_distillation.py \
  031-matclaw-cips-active-distillation/tests/test_active_contract.py
git commit -m "feat: checkpoint Case 031 active distillation"
```

### Task 6: Harden the Hidden Verifier Against the Old False Success

**Files:**
- Modify: `031-matclaw-cips-active-distillation/tests/verifier.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test_outputs.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test.sh`

**Interfaces:**
- Keep `verify(submission: Path, expected_profile: str | None = None, teacher_model: Path | None = None) -> dict`.
- Report adds `active_iterations`, `recomputed_final_mae_eV_A`, `source_relative_error`, and `artifact_hashes`.

- [ ] **Step 1: Write RED negative fixtures**

Add tests rejecting: paper history with only iteration 0; empty selection before
iteration 1; selected hash not matching the recomputed candidate; duplicated
selected hashes; out-of-band deviation; wrong growth; forged label; identical
committee models; changed checkpoint hash; and final MAE outside acceptance.

- [ ] **Step 2: Verify RED**

Run the case test suite in the pinned CPU image. Expected: zero-active-iteration
and checkpoint forgery fixtures expose current verifier gaps.

- [ ] **Step 3: Implement independent recomputation**

For each active transition, load raw trajectories, recompute deviations from
both delivered models, reconstruct selected hashes, verify teacher labels, and
compare exact dataset growth. For paper mode require history length at least 2,
one or more selected frames, final MAE `<0.10`, and source-relative error
`<=0.25`.

- [ ] **Step 4: Remove conditional skips that exempt final iteration zero**

Tests must fail rather than skip when a paper submission has no exploration
evidence. Smoke fixtures may remain non-formal but must still carry a valid small
active transition.

- [ ] **Step 5: Run GREEN tests**

Run:

```bash
docker run --rm -v "$PWD/031-matclaw-cips-active-distillation:/case" \
  dftworld-base-matclaw-cips:2.2.11-cpu \
  /opt/matclaw/bin/python -m pytest -q /case/tests
```

Expected: all positive and negative tests pass with no skips caused by missing
paper active evidence.

- [ ] **Step 6: Commit**

```bash
git add 031-matclaw-cips-active-distillation/tests
git commit -m "test: reject inactive Case 031 paper results"
```

### Task 7: Align the Independent Alternative Implementation

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/alt_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/tests/test_outputs.py`

**Interfaces:**
- Alternative CLI and output schema match the primary contract but do not import
  `run_distillation.py` or share its orchestration functions.

- [ ] **Step 1: Add a RED alternative smoke test**

Run the alternative with a smoke profile and distinct seed, then require the
same hidden verifier to accept a real active transition and exact growth.

- [ ] **Step 2: Verify RED**

Expected: the current alternative can stop at iteration zero or lacks new fields.

- [ ] **Step 3: Implement the external contract independently**

Keep `DistillState`, `dpdata` writing, and stage functions. Add bounded batches,
mandatory retraining, explicit seed, and checkpoint-compatible output fields
without importing primary workflow code.

- [ ] **Step 4: Run primary, alternative, and negative tests**

Expected: both smoke outputs pass the same verifier; every forged output fails.

- [ ] **Step 5: Commit**

```bash
git add 031-matclaw-cips-active-distillation/solution/alt_distillation.py \
  031-matclaw-cips-active-distillation/tests/test_outputs.py
git commit -m "feat: align alternative Case 031 workflow"
```

### Task 8: Run CPU Smoke and Performance-Safe Diagnostics

**Files:**
- Modify only if evidence exposes a defect: Case 031 solution/tests/profile files.
- Keep generated smoke outputs outside Git.

**Interfaces:**
- Smoke seeds: `2026081101` primary and `2026081102` alternative.

- [ ] **Step 1: Run the primary smoke reference directly**

Use a fresh temporary workspace containing only public inputs and mount solution
read-only. Expected: at least one selected frame, one retrained iteration,
hidden verifier valid, and `formal_result=false`.

- [ ] **Step 2: Run the alternative smoke independently**

Use another empty workspace and the second seed. Expected: same contract and no
shared model, trajectory, checkpoint, or result files.

- [ ] **Step 3: Exercise resume**

Interrupt a smoke run after a declared completed stage, resume with the same
identity, and verify success. Retry with a different seed and expect a fail-closed
identity error.

- [ ] **Step 4: Run the full repository contract subset**

```bash
pytest -q tests/test_matclaw_case_contracts.py -k 031
git diff --check -- 031-matclaw-cips-active-distillation
```

Expected: all selected tests pass and no whitespace errors exist.

### Task 9: Add the Restricted Sandbox-to-HPC Controller

**Files:**
- Create: `scripts/matclaw_hpc_controller.py`
- Create: `scripts/hpc/031_matclaw_gpu.slurm`
- Create: `tests/test_matclaw_hpc_controller.py`
- Create: `base-env-build/matclaw-cips-controller/Dockerfile`
- Modify: `eval.py`
- Modify: `031-matclaw-cips-active-distillation/Dockerfile`
- Reuse: `scripts/ablation/transport/slurm_transport.py`

**Interfaces:**
- `MatClawHpcController.stage(local_run: Path, remote_run_id: str) -> str`
- `MatClawHpcController.submit(remote_run_id: str, run_kind: str) -> str`
- `MatClawHpcController.status(job_id: str) -> JobState`
- `MatClawHpcController.log(job_id: str, tail: int = 200) -> str`
- `MatClawHpcController.cancel(job_id: str) -> None`
- `MatClawHpcController.fetch(remote_run_id: str, local_run: Path) -> dict[str, str]`

- [ ] **Step 1: Write RED controller-security tests**

Test rejection of `..` and absolute remote run IDs, non-031 cases, arbitrary
remote commands, partitions other than `gpu`, more than one GPU, fetch before
`COMPLETED`, and an artifact whose fetched SHA-256 differs from the remote
manifest. Test that `UNKNOWN` is non-terminal and polling continues. Test that
the local controller image contains `ssh`, `rsync`, and the controller CLI but
contains no private-key file or hidden `solution/` tree.

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/test_matclaw_hpc_controller.py`

Expected: collection fails because `scripts.matclaw_hpc_controller` is absent.

- [ ] **Step 3: Build the local Docker control layer**

Create a Case 031 controller base derived from the pinned CPU MatClaw image. Add
OpenSSH client and rsync, install `slurm_transport.py` plus the restricted
controller CLI below `/opt/dftworld/controller/`, and keep the task Dockerfile's
only task-data copy as `COPY public/ /app/`. Do not copy `solution/`,
`reference/`, tests, SSH config, known hosts, or private keys into the image.

- [ ] **Step 4: Forward authentication at container launch**

Extend the Docker launch path for Case 031 controller mode with the host SSH
agent socket mounted at `/run/dftworld-ssh-agent`, `SSH_AUTH_SOCK` set to that
path, and SSH config/known-hosts mounted read-only. Fail before container start
when the socket or host alias is unavailable. Never fall back to copying a key.

- [ ] **Step 5: Implement the in-container adapter**

Construct `SshSlurmTransport` with SSH alias `<site-alias>`, `sync='sync_back'`,
and remote workspace
`/public/home/<site-user>/dftworld2-runs/matclaw-031/<validated-run-id>`.
The CLI executes inside the local Case 031 Docker container. Its request/response
JSON contains run IDs, canonical states, log tails, and artifact hashes only. It
contains no key path, password, raw SSH option, or arbitrary shell field.

- [ ] **Step 6: Implement the fixed Slurm entry point**

The generated/staged script must contain the equivalent of:

```bash
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=slurm-%j.out
set -euo pipefail
APPTAINER=/public/software/apptainer/bin/apptainer
exec "$APPTAINER" exec --nv --containall \
  --bind "$RUN_DIR:/app" --bind "$SOLUTION_DIR:/solution:ro" \
  "$MATCLAW_SIF" /solution/solve.sh
```

Resolve `RUN_DIR`, `SOLUTION_DIR`, and `MATCLAW_SIF` from controller-generated,
path-confined environment values; reject missing values before `sbatch`.

- [ ] **Step 7: Run mocked transport tests and a real one-minute canary**

First pass all local tests. Then launch the local Case 031 Docker controller,
stage a script that prints hostname, `CUDA_VISIBLE_DEVICES`, and GPU name,
submit it from inside the container, poll via `sacct`, fetch the log, and require
`A100-SXM4-80GB` plus `COMPLETED`.

- [ ] **Step 8: Commit**

```bash
git add scripts/matclaw_hpc_controller.py scripts/hpc/031_matclaw_gpu.slurm \
  tests/test_matclaw_hpc_controller.py base-env-build/matclaw-cips-controller/Dockerfile \
  eval.py 031-matclaw-cips-active-distillation/Dockerfile
git commit -m "feat: add Case 031 HPC controller"
```

### Task 10: Build the Locked Apptainer Runtime and Run One Diagnostic Paper Profile

**Files:**
- Generated outside Git: qualification JSON and diagnostic artifact bundle.
- Modify the declared profile only after reviewed diagnostic evidence.

**Interfaces:**
- Diagnostic seed: `2026081199`.
- GPU parity: energy and maximum force-component differences `<1e-6`.

- [ ] **Step 1: Produce and lock the SIF**

Publish or otherwise expose the pinned GPU OCI image by immutable digest, then
create the SIF with Apptainer 1.4.0. Record the OCI `name@sha256:` identity and
`sha256sum` of the SIF. Run both remaining tasks against that exact SIF path and
hash; never rebuild it between runs.

- [ ] **Step 2: Pass GPU qualification**

Require GPU visibility, pinned software versions, CPU/GPU parity, finite
100-step MD, and a measured runtime estimate. Set timeout to at least `1.5x`
the estimate.

- [ ] **Step 3: Run one paper diagnostic from a clean output root**

Mark it `evidence_class=diagnostic`. Record per-batch deviation quantiles,
selected counts, data growth, iteration MAEs, wall time, and GPU identity.

- [ ] **Step 4: Apply the diagnostic decision tree**

- If in-band frames appear and retrained MAE passes: freeze the profile.
- If all deviations stay below `0.05`: add a reviewed higher-temperature or
  longer declared exploration batch; do not weaken the lower bound.
- If all deviations exceed `0.15`: improve initial committee training or add a
  reviewed intermediate-temperature batch; do not weaken the upper bound.
- If selection succeeds but MAE fails: increase declared training steps or
  inspect label/data integrity before changing exploration.
- If MD is non-finite: reject the condition and fix stability before continuing.

- [ ] **Step 5: Rerun CPU tests after any profile change**

Every change starts a new diagnostic attempt. Never overwrite or promote a
failed diagnostic result.

### Task 11: Freeze and Execute Two Formal Reproductions

**Files:**
- Generated locally/artifact store: two formal bundles and manifests.
- Modify via generator: `031-matclaw-cips-active-distillation/VALIDATION.json`
- Modify via generator: `031-matclaw-cips-active-distillation/benchmark_valid.json`

**Interfaces:**
- Formal seeds: `2026081101` and `2026081102`.
- Both runs use the same full Git commit, GPU OCI `name@sha256:` digest, and SIF SHA-256.

- [ ] **Step 1: Freeze source and images**

Require clean `git status`, passing Case 031, controller, and repository contract
tests, `git diff --check`, immutable OCI digest, SIF SHA-256, CPU verifier
digest, and stored GPU qualification report.

- [ ] **Step 2: Run formal reproduction 1**

Use an empty run-1 workspace and seed `2026081101`. Run direct reference
execution, hidden CPU verification, artifact hashing, and manifest generation.
Expected: all single-run gates pass.

- [ ] **Step 3: Prove run-2 isolation**

Use a new empty path; reject symlinks or mounts into run 1 and verify no run-1
hash appears as a reused checkpoint/model/trajectory artifact.

- [ ] **Step 4: Run formal reproduction 2**

Use seed `2026081102` with the identical frozen commit and image digests.
Expected: all single-run gates pass.

- [ ] **Step 5: Derive final validation**

The fail-closed generator checks both active iteration counts, data growth,
selection bands, hidden reports, individual MAE thresholds, source-relative
errors, different seeds/workspaces, matching commit/OCI/SIF identities, Slurm
job IDs and `COMPLETED` states, and MAE difference `<=0.01`. Missing or
mismatched evidence leaves `benchmark_valid=false`.

- [ ] **Step 6: Run final verification and review**

```bash
pytest -q 031-matclaw-cips-active-distillation/tests
pytest -q tests/test_matclaw_case_contracts.py -k 031
pytest -q tests/test_matclaw_hpc_controller.py
python -m json.tool 031-matclaw-cips-active-distillation/VALIDATION.json >/dev/null
python -m json.tool 031-matclaw-cips-active-distillation/benchmark_valid.json >/dev/null
git diff --check
```

Expected: all commands pass and `benchmark_valid=true` only when both formal
artifact bundles remain present and hash-valid.

## Checkpoints and Estimated Time

1. Tasks 1–7: implementation and verifier repair, 3–6 hours.
2. Task 8: CPU smoke and resume validation, 1–2 hours.
3. Task 9: controller, sync, and Slurm canary, 2–4 hours.
4. Task 10: SIF build, GPU qualification, and diagnostic paper run, 2–5 hours.
5. Task 11: two formal runs, comparison, and review, 3–7 hours.

Expected total: 11–24 hours, with measured GPU performance replacing the run
estimates before formal execution.
