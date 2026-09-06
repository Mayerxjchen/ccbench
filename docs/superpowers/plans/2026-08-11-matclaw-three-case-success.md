# MatClaw 031–033 Strict Construction Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Cases 031–033 into reproducible, fail-closed scientific benchmarks whose final status is supported by two independent paper-profile GPU reproductions of the source results.

**Architecture:** Keep public task inputs, hidden reference implementations, raw run artifacts, hidden verification, and derived validation as separate trust boundaries. Run present in-flight jobs only as diagnostics; after a clean commit and immutable GPU image are frozen, execute two isolated formal runs per case and derive each case's status from artifact hashes and independently recomputed science.

**Tech Stack:** Python 3.11, pytest, ASE, DeePMD-kit 2.2.11, TensorFlow 2.16.2, NumPy/SciPy, Docker with NVIDIA Container Toolkit, Bash, TOML, JSON, Git.

## Global Constraints

- Do not stop, modify, rename, or reuse any currently running container or `jobs/` workspace.
- Treat every pre-freeze run as `diagnostic`; diagnostic evidence can never satisfy formal gates G7, G8, or G12.
- Formal runs use one clean Git commit, one immutable GPU image digest, and two different declared seeds per case.
- Formal run 2 must not read trajectories, models, checkpoints, results, or derived data from formal run 1.
- The GPU execution image is the RepoDigest resolved from `dftworld-base-matclaw-cips:2.2.11-gpu` (a `name@sha256:` identity); never retag it as the CPU image.
- The CPU verifier image and GPU execution image are distinct identities recorded in every formal manifest.
- `public/` never contains `solution/`, `reference/`, verifier code, a future Case 033 search path, or a source-optimal answer.
- Missing artifacts, missing bundles, dirty source state, digest mismatches, verifier failures, and absent second runs all force `benchmark_valid=false`.
- Case 031 requires at least one genuine selection/relabel/retrain iteration, final force MAE `<0.10 eV/angstrom`, source-relative error `<=25%` from `0.098`, and cross-run MAE difference `<=0.01`.
- Case 032 requires the 360-atom pilot plus all 13 temperatures, each Tc within `261.3 +/- 10 K`, and cross-run Tc difference `<=10 K`.
- Case 033 requires seven sequential rounds and fourteen jobs, at most two jobs per round, best field `-0.16 +/- 0.02 V/angstrom`, best temperature `50 +/- 20 K`, slope `>0.3 ps/site`, and cross-run slope difference `<=0.05 ps/site`.
- GPU qualification requires visible hardware, CPU/GPU energy and maximum force-component differences `<1e-6`, a finite 100-step MD trajectory, and timeouts at least `1.5x` the measured estimate.
- Do not include Case 034, AI2Kit, portable-skill migration, or unrelated repository cleanup in this branch.

---

## File Map

- `benchmark/sources/matclaw/acceptance.json`: single machine-readable scientific acceptance policy for 031–033.
- `scripts/matclaw_validation.py`: evidence schema checks, hashing, cross-run comparisons, validation derivation, and CLI.
- `scripts/run_matclaw_reference.sh`: isolated public-input staging and hidden-solution execution for smoke, diagnostic, and formal runs.
- `scripts/run_gpu_paper.sh`: immutable GPU image build/qualification and formal reference-run orchestration; no CPU-tag replacement.
- `eval.py`: parse `environment.gpus`, pass explicit Docker GPU device requests, and record image/resource identity.
- `tests/test_matclaw_validation.py`: fail-closed and cross-run validation tests using small synthetic manifests.
- `tests/test_matclaw_case_contracts.py`: public isolation, resource, timeout, runner, and GPU contract tests.
- `base-env-build/matclaw-cips-gpu/Dockerfile`: pinned GPU runtime with build-time CPU smoke and runtime GPU qualification support.
- `base-env-build/matclaw-cips-gpu/qualify_gpu.py`: visibility, parity, finite-MD, versions, and timing report.
- `docs/gpu-execution.md`: exact build, qualification, freeze, formal-run, resume, and failure procedures.
- `031-matclaw-cips-active-distillation/solution/run_distillation.py`: genuine active-loop implementation and resumable checkpoints.
- `032-matclaw-cips-curie-temperature/solution/run_curie.py`: exact paper sweep with atomic per-temperature checkpoints and resume.
- `033-matclaw-cips-domain-wall-search/solution/search_policy.py`: hidden adaptive proposal policy.
- `033-matclaw-cips-domain-wall-search/solution/run_search.py`: measured-feedback-only round execution and audit chain.
- Each case's `tests/verifier.py`: raw-artifact scientific recomputation and anti-forgery checks.
- Each case's `VALIDATION.json` and `benchmark_valid.json`: generated outputs only.
- `evidence/matclaw/.gitignore`: retain directory shape while excluding large raw bundles.
- `evidence/matclaw/diagnostic/index.json`: hashes and outcomes of the current in-flight diagnostic jobs.
- `evidence/matclaw/formal/031/run-1/manifest.json` (and the corresponding `031` run-2, `032` run-1/run-2, and `033` run-1/run-2 paths): immutable local formal manifests beside their artifact bundles.

### Task 1: Freeze the Execution Boundary Without Disturbing Live Jobs

**Files:**
- Create: `evidence/matclaw/.gitignore`
- Create: `evidence/matclaw/diagnostic/index.json`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: current Git worktree, Docker container list, and `jobs/` directories.
- Produces: a clean worktree at `/Users/chenxuanjie/案例测试/dftworld2-matclaw-formal` and a diagnostic inventory whose entries contain `run_id`, `case`, `workspace`, `status`, `started_from_commit`, and `evidence_class`.

- [ ] **Step 1: Inventory rather than alter current work**

Run:

```bash
cd /Users/chenxuanjie/案例测试/dftworld2
git status --short
docker ps --format '{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}'
find jobs -maxdepth 2 -type d -name '*031*' -o -name '*032*' -o -name '*033*'
```

Expected: the dirty worktree and active 031–033 containers/workspaces are printed; no command changes them.

- [ ] **Step 2: Create the isolated worktree using the required skill**

Invoke `superpowers:using-git-worktrees`, then run its safe worktree creation flow with:

```bash
git worktree add -b matclaw-031-033-formal /Users/chenxuanjie/案例测试/dftworld2-matclaw-formal HEAD
git -C /Users/chenxuanjie/案例测试/dftworld2-matclaw-formal status --short
```

Expected: the second command prints nothing. If the branch or path already exists, inspect it and reuse it only when it is clean and points at the intended HEAD; never delete an existing worktree.

- [ ] **Step 3: Add the evidence exclusion contract**

Use `apply_patch` to add:

```gitignore
# Raw scientific bundles are local/remote evidence, not Git payloads.
formal/**/artifacts/
formal/**/workspace/
diagnostic/**/artifacts/
diagnostic/**/workspace/
```

to `evidence/matclaw/.gitignore`, and add `/evidence/matclaw/formal/**/artifacts/` plus `/evidence/matclaw/formal/**/workspace/` to the repository `.gitignore`.

- [ ] **Step 4: Record current runs as diagnostics**

Create `evidence/matclaw/diagnostic/index.json` with schema:

```json
{
  "schema_version": "1.0",
  "evidence_class": "diagnostic",
  "formal_gate_eligible": false,
  "runs": []
}
```

Populate `runs` only from read-only inspection. Each entry must preserve the exact workspace path and may use `"status": "running"`; it must never say `formal` or `benchmark_valid`.

- [ ] **Step 5: Verify and commit the isolation metadata**

Run:

```bash
python -m json.tool evidence/matclaw/diagnostic/index.json >/dev/null
git diff --check
git add .gitignore evidence/matclaw/.gitignore evidence/matclaw/diagnostic/index.json
git commit -m "chore: isolate MatClaw formal evidence"
```

Expected: JSON parsing and `git diff --check` succeed; the commit contains no `jobs/` file and no scientific artifact.

### Task 2: Implement Fail-Closed Validation and Invalidate the Stale 031 Claim

**Files:**
- Create: `benchmark/sources/matclaw/acceptance.json`
- Create: `scripts/matclaw_validation.py`
- Create: `tests/test_matclaw_validation.py`
- Modify: `031-matclaw-cips-active-distillation/VALIDATION.json`
- Modify: `031-matclaw-cips-active-distillation/benchmark_valid.json`
- Modify: `032-matclaw-cips-curie-temperature/VALIDATION.json`
- Modify: `032-matclaw-cips-curie-temperature/benchmark_valid.json`
- Modify: `033-matclaw-cips-domain-wall-search/VALIDATION.json`
- Modify: `033-matclaw-cips-domain-wall-search/benchmark_valid.json`

**Interfaces:**
- Produces: `sha256_file(path: Path) -> str`, `validate_run_manifest(manifest_path: Path, case_id: str, policy: dict) -> dict`, `compare_formal_runs(case_id: str, left: dict, right: dict, policy: dict) -> dict`, and `derive_case(case_dir: Path, evidence_root: Path, policy_path: Path) -> dict`.
- CLI: `python scripts/matclaw_validation.py derive --case 031|032|033|all --evidence-root evidence/matclaw/formal --write`.

- [ ] **Step 1: Write failure-first validation tests**

Add tests that create temporary manifests and assert these exact outcomes:

```python
def test_missing_formal_bundle_forces_false(tmp_path):
    report = derive_case(case_dir=case_fixture(tmp_path, "031"),
                         evidence_root=tmp_path / "formal",
                         policy_path=POLICY)
    assert report["benchmark_valid"] is False
    assert report["state"] == "constructed"
    assert "two formal runs required" in report["reasons"]

def test_diagnostic_manifest_is_never_formal(tmp_path):
    manifest = write_manifest(tmp_path, evidence_class="diagnostic", verifier_valid=True)
    result = validate_run_manifest(manifest, "031", load_policy())
    assert result["eligible"] is False
    assert "evidence_class" in result["errors"]

def test_digest_tampering_forces_false(tmp_path):
    manifest = write_complete_031_fixture(tmp_path)
    (manifest.parent / "artifacts" / "result.json").write_text("{}")
    result = validate_run_manifest(manifest, "031", load_policy())
    assert result["eligible"] is False
    assert any("sha256" in error for error in result["errors"])
```

Also test same commit/digest, distinct seeds, isolation roots, all case-specific thresholds, and generated `benchmark_valid.json == {"benchmark_valid": VALIDATION["benchmark_valid"]}`.

- [ ] **Step 2: Run tests and confirm the module is absent**

Run: `pytest -q tests/test_matclaw_validation.py`

Expected: collection fails because `scripts.matclaw_validation` does not exist.

- [ ] **Step 3: Add the exact acceptance policy**

Create `benchmark/sources/matclaw/acceptance.json` with these numeric values:

```json
{
  "schema_version": "1.0",
  "formal_runs_required": 2,
  "same_commit_required": true,
  "same_gpu_image_digest_required": true,
  "distinct_seed_required": true,
  "031": {"source_mae_eV_A": 0.098, "max_mae_eV_A": 0.10, "max_source_relative_error": 0.25, "max_cross_run_mae_eV_A": 0.01, "min_active_iterations": 1},
  "032": {"source_Tc_K": 261.3, "max_source_abs_error_K": 10.0, "max_cross_run_abs_error_K": 10.0, "temperatures_K": [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600], "atom_count": 360},
  "033": {"source_Ez_V_A": -0.16, "max_Ez_abs_error_V_A": 0.02, "source_temperature_K": 50, "max_temperature_abs_error_K": 20, "min_slope_ps_per_site": 0.3, "max_cross_run_slope_ps_per_site": 0.05, "paper_rounds": 7, "paper_jobs": 14, "max_jobs_per_round": 2}
}
```

- [ ] **Step 4: Implement manifest verification and derivation**

Implement the four interfaces above. A formal manifest must contain:

```python
REQUIRED_MANIFEST_KEYS = {
    "schema_version", "evidence_class", "case", "profile", "seed", "run_id",
    "started_at", "finished_at", "exit_status", "git_commit", "git_clean",
    "gpu_image", "gpu_image_digest", "cpu_verifier_image",
    "cpu_verifier_image_digest", "hardware", "software", "command",
    "workspace_identity", "artifacts", "verifier_report"
}
```

Require `evidence_class == "formal"`, `profile == "paper"`, `exit_status == 0`, `git_clean is True`, a `sha256:` image digest, `verifier_report.valid is True`, and exact SHA-256 matches for every artifact. Resolve artifact paths relative to the manifest directory and reject absolute paths or `..` components. `derive_case` must emit gates G0–G12 and compute `benchmark_valid = all(gate["passed"] for gate in gates.values())`; it must never read a pre-existing gate boolean as evidence.

- [ ] **Step 5: Prove the tests pass and derive all current statuses**

Run:

```bash
pytest -q tests/test_matclaw_validation.py
python scripts/matclaw_validation.py derive --case all --evidence-root evidence/matclaw/formal --write
python -m json.tool 031-matclaw-cips-active-distillation/VALIDATION.json >/dev/null
```

Expected: tests pass; all three cases are `benchmark_valid=false`; Case 031 includes a missing/two-formal-runs reason rather than claiming success.

- [ ] **Step 6: Commit**

```bash
git add benchmark/sources/matclaw/acceptance.json scripts/matclaw_validation.py tests/test_matclaw_validation.py \
  031-matclaw-cips-active-distillation/{VALIDATION.json,benchmark_valid.json} \
  032-matclaw-cips-curie-temperature/{VALIDATION.json,benchmark_valid.json} \
  033-matclaw-cips-domain-wall-search/{VALIDATION.json,benchmark_valid.json}
git commit -m "feat: derive MatClaw validation from formal evidence"
```

### Task 3: Add One Reproducible Hidden Reference Runner

**Files:**
- Create: `scripts/run_matclaw_reference.sh`
- Modify: `tests/test_matclaw_case_contracts.py`
- Modify: each case's `solution/solve.sh`

**Interfaces:**
- CLI: `scripts/run_matclaw_reference.sh --case 031|032|033 --profile smoke|paper --seed INT --image IMAGE@DIGEST --output ABS_PATH --evidence-class diagnostic|formal [--gpus DEVICE]`.
- Each `solution/solve.sh` consumes `/app`, `MATCLAW_PROFILE`, `MATCLAW_SEED`, and `MATCLAW_OUTPUT`; it writes `result.json` under `MATCLAW_OUTPUT`.

- [ ] **Step 1: Add contract tests**

Test that the runner contains read-only solution mounting and public staging, rejects a relative output path, rejects `formal` with a tag-only image, and rejects `formal` on a dirty worktree. Also assert all three `solve.sh` files read `MATCLAW_SEED` and do not access repository-relative `reference/` paths.

- [ ] **Step 2: Confirm tests fail**

Run: `pytest -q tests/test_matclaw_case_contracts.py -k reference_runner`

Expected: FAIL because `scripts/run_matclaw_reference.sh` is missing.

- [ ] **Step 3: Implement staging and hidden execution**

The runner must:

```bash
case_dir="$repo_root/${case_id}-matclaw-cips-${case_slug}"
test "${output#/}" != "$output" || { echo "--output must be absolute" >&2; exit 2; }
mkdir -p "$output/workspace" "$output/artifacts"
cp -a "$case_dir/public/." "$output/workspace/"
docker_args=(run --rm --mount "type=bind,src=$output/workspace,dst=/app" \
  --mount "type=bind,src=$case_dir/solution,dst=/solution,readonly" \
  --env "MATCLAW_PROFILE=$profile" --env "MATCLAW_SEED=$seed" \
  --env "MATCLAW_OUTPUT=/app")
```

For `formal`, require a clean Git state, `profile=paper`, `evidence_class=formal`, an image reference containing `@sha256:`, an empty output workspace, and `--gpus`; append `--gpus "device=$gpu_device"`. Do not mount the repository root. After execution, hash artifacts and write a preliminary manifest with `verifier_report.valid=false` until Task 5 runs the hidden verifier.

- [ ] **Step 4: Make solve entry points deterministic**

Each `solve.sh` must require the same environment variables. For Case 031 the exact entry point is:

```bash
profile="${MATCLAW_PROFILE:?MATCLAW_PROFILE is required}"
seed="${MATCLAW_SEED:?MATCLAW_SEED is required}"
output="${MATCLAW_OUTPUT:-/app}"
exec /opt/matclaw/bin/python /solution/run_distillation.py \
  --profile "$profile" --seed "$seed" --output "$output"
```

Case 032 substitutes `/solution/run_curie.py`; Case 033 substitutes `/solution/run_search.py`. All other lines stay identical.

- [ ] **Step 5: Run shell checks and CPU smoke integration**

Run:

```bash
bash -n scripts/run_matclaw_reference.sh
bash -n 031-matclaw-cips-active-distillation/solution/solve.sh
bash -n 032-matclaw-cips-curie-temperature/solution/solve.sh
bash -n 033-matclaw-cips-domain-wall-search/solution/solve.sh
pytest -q tests/test_matclaw_case_contracts.py -k 'reference_runner or images_expose'
```

Expected: all checks pass. Then run one CPU smoke per case into three new `/private/tmp/matclaw-smoke-*` paths; expected: each exits 0 and writes `result.json` with `formal_result=false`.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_matclaw_reference.sh tests/test_matclaw_case_contracts.py \
  031-matclaw-cips-active-distillation/solution/solve.sh \
  032-matclaw-cips-curie-temperature/solution/solve.sh \
  033-matclaw-cips-domain-wall-search/solution/solve.sh
git commit -m "feat: add isolated MatClaw reference runner"
```

### Task 4: Remove Public Answer Leakage and Lock Repository Contracts

**Files:**
- Modify: `033-matclaw-cips-domain-wall-search/public/run_profiles.json`
- Create: `033-matclaw-cips-domain-wall-search/solution/search_policy.py`
- Modify: `033-matclaw-cips-domain-wall-search/solution/run_profiles.json`
- Modify: `tests/test_matclaw_case_contracts.py`

**Interfaces:**
- Produces: `propose_round(previous: list[dict], round_index: int, seed: int) -> list[tuple[float, int]]` in hidden solution code.
- Public paper profile retains only `supercell`, `max_iterations`, `max_jobs_per_iteration`, `md_steps`, `seed`, `start`, and `bounds`.

- [ ] **Step 1: Write the leak test**

```python
def test_case_033_public_profile_does_not_reveal_answer_or_future_path():
    public = json.loads((ROOT / CASES[2] / "public/run_profiles.json").read_text())
    serialized = json.dumps(public)
    assert "search_path" not in serialized
    assert "source-path-replay" not in serialized
    assert "-0.16" not in serialized
    assert public["paper"]["bounds"] == {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]}
```

- [ ] **Step 2: Run and observe failure**

Run: `pytest -q tests/test_matclaw_case_contracts.py::test_case_033_public_profile_does_not_reveal_answer_or_future_path`

Expected: FAIL because `search_path` and `-0.16` are public.

- [ ] **Step 3: Split public constraints from hidden policy**

Replace the public paper object with only the allowed fields. Put proposal logic in `search_policy.py`; it may use the source-derived policy internally, but its output for rounds 2–7 must be a function of measured prior rows. Reject `round_index > 0` when `previous` contains no completed preceding round.

- [ ] **Step 4: Rebuild source/input locks and test isolation**

Run the repository's existing source/view generator if it is the canonical owner of public hashes; otherwise update only the Case 033 public-profile hash in its source lock. Then run:

```bash
pytest -q tests/test_matclaw_case_contracts.py -k 'public_profile or profiles_and_source_locks or images_expose'
rg -n -- '-0\.16|search_path|source-path-replay' 033-matclaw-cips-domain-wall-search/public
```

Expected: tests pass and `rg` has no matches.

- [ ] **Step 5: Commit**

```bash
git add 033-matclaw-cips-domain-wall-search/public/run_profiles.json \
  033-matclaw-cips-domain-wall-search/solution/search_policy.py \
  033-matclaw-cips-domain-wall-search/solution/run_profiles.json \
  tests/test_matclaw_case_contracts.py
git commit -m "fix: hide Case 033 adaptive search answer"
```

### Task 5: Harden Hidden Verifiers and Add Negative Artifact Fixtures

**Files:**
- Modify: all three case `tests/verifier.py` files
- Modify: all three case `tests/test_outputs.py` files
- Modify: `032-matclaw-cips-curie-temperature/tests/test_analysis.py`
- Modify: `033-matclaw-cips-domain-wall-search/tests/test_analysis.py`

**Interfaces:**
- Each verifier keeps `verify(submission: Path, expected_profile: str | None = None) -> dict`.
- Verifier output always includes `valid: bool`, `errors: list[str]`, `recomputed: dict|list`, and `artifact_hashes: dict[str, str]`.

- [ ] **Step 1: Add forged-artifact tests before verifier changes**

For each case, start from its smallest valid smoke fixture and independently alter: `result.json` only, one raw trajectory/dataset byte, the model identity, and the declared profile. Assert `verify(...)["valid"] is False`. For Case 033 also forge `decision_basis` text without a prior-round digest; for Case 031 create zero active iterations; for Case 032 delete one temperature trajectory.

- [ ] **Step 2: Run and capture failures**

Run:

```bash
pytest -q 031-matclaw-cips-active-distillation/tests
pytest -q 032-matclaw-cips-curie-temperature/tests
pytest -q 033-matclaw-cips-domain-wall-search/tests
```

Expected: the new negative tests fail against at least the existing zero-iteration, incomplete-grid, and narrative-only feedback checks.

- [ ] **Step 3: Enforce raw-evidence semantics**

Case 031: require paper history to include iteration 0 plus at least one later iteration, require selected configuration hashes to be a subset of recomputed in-band exploration frames, require exact teacher-labelled data growth, and recompute held-out MAE from delivered student models.

Case 032: require pilot plus the exact 13-temperature grid, exact supercell/atom/frame counts, finite frames, per-trajectory SHA-256, independently recomputed `Q(T)`, both Tc estimators, and source tolerance.

Case 033: require contiguous rounds 1–7, exactly two jobs per round, and for every round after 1 require `decision_input_sha256` equal to the SHA-256 of the canonical JSON summary of all preceding measured jobs. Recompute flips and slope from trajectories and require the declared best row to equal the recomputed best row.

- [ ] **Step 4: Run all positive and negative verifier tests**

Run the three commands from Step 2.

Expected: all case tests pass; every tampered fixture returns an explicit error without raising an uncaught exception.

- [ ] **Step 5: Commit**

```bash
git add 031-matclaw-cips-active-distillation/tests \
  032-matclaw-cips-curie-temperature/tests \
  033-matclaw-cips-domain-wall-search/tests
git commit -m "test: make MatClaw verifiers evidence-driven"
```

### Task 6: Build and Enforce the Explicit GPU Runtime Contract

**Files:**
- Create: `base-env-build/matclaw-cips-gpu/qualify_gpu.py`
- Modify: `base-env-build/matclaw-cips-gpu/Dockerfile`
- Modify: `scripts/run_gpu_paper.sh`
- Modify: `eval.py`
- Modify: all three `task.toml` files
- Modify: `docs/gpu-execution.md`
- Modify: `tests/test_matclaw_case_contracts.py`

**Interfaces:**
- `TaskSpec.gpus: int` parsed from `[environment].gpus`.
- `docker_gpu_args(gpus: int) -> list[str]`, returning `[]` for zero and `['--gpus', 'device=0']` for one.
- Qualification CLI: `/opt/matclaw/bin/python /opt/matclaw/qualify_gpu.py --structure PATH --model PATH --supercell X Y Z --temperature K --steps 100 --json-out PATH`.

- [ ] **Step 1: Add resource and retagging regression tests**

Assert all task files have `gpus = 1`; their agent and verifier timeouts are at least the committed measured timeout values; `eval.load_task()` exposes `gpus`; GPU runner contains no `docker tag`, `PINNED_TAG`, `--force-retag`, or `-t ...:cpu`; and `docker_gpu_args(1)` is explicit.

- [ ] **Step 2: Confirm failures**

Run: `pytest -q tests/test_matclaw_case_contracts.py -k 'gpu or timeout or resources'`

Expected: FAIL because metadata currently says `gpus=0`, eval lacks GPU allocation, and the runner retags the CPU name.

- [ ] **Step 3: Implement GPU qualification**

`qualify_gpu.py` must evaluate the locked structure once with forced CPU and once with GPU, then emit:

```json
{
  "gpu_visible": true,
  "gpu_name": "...",
  "energy_abs_diff_eV": 0.0,
  "max_force_component_abs_diff_eV_A": 0.0,
  "md_steps": 100,
  "md_finite": true,
  "elapsed_s": 0.0,
  "seconds_per_step": 0.0,
  "versions": {"python": "...", "tensorflow": "...", "deepmd": "...", "ase": "..."}
}
```

Exit nonzero unless GPU is visible, both differences are `<1e-6`, all MD values are finite, and the trajectory has 101 frames at interval 1.

- [ ] **Step 4: Pass the GPU device through eval**

Add `gpus: int = 0` to `TaskSpec`, parse it in `load_task`, reject negative values, and make the Docker backend append `docker_gpu_args(task.gpus)` to `docker run`. Record `requested_gpus` in run metadata. Do not rely on Docker's default runtime.

- [ ] **Step 5: Rewrite GPU orchestration around immutable identity**

`run_gpu_paper.sh` must build only `dftworld-base-matclaw-cips:2.2.11-gpu`, resolve:

```bash
gpu_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$gpu_image")"
```

and fail if no `@sha256:` identity is available. It must call `qualify_gpu.py`, calculate `ceil(1.5 * estimated_seconds)`, print and persist the estimate, and call `run_matclaw_reference.sh` with the digest and `--gpus 0`. No CPU tag is changed.

- [ ] **Step 6: Set resource metadata from the probe**

Set `gpus = 1` for all three cases. After the qualification/performance probe, set each `agent.timeout_sec` and `verifier.timeout_sec` to integers no lower than 1.5 times its measured stage duration; keep `build_timeout_sec >= 1800`. Document the measured seconds and chosen values in `docs/gpu-execution.md` and the qualification JSON, not in an unverified estimate.

- [ ] **Step 7: Run tests and GPU qualification on the formal host**

Run:

```bash
pytest -q tests/test_matclaw_case_contracts.py -k 'gpu or timeout or resources'
bash -n scripts/run_gpu_paper.sh
scripts/run_gpu_paper.sh --case 031 --profile smoke --build --probe-steps 100
scripts/run_gpu_paper.sh --case 032 --profile smoke --probe-steps 100
scripts/run_gpu_paper.sh --case 033 --profile smoke --probe-steps 100
```

Expected: tests pass; each probe emits `gpu_visible=true`, parity values below `1e-6`, `md_finite=true`, and a resolved digest. Any failed probe blocks Tasks 11–12.

- [ ] **Step 8: Commit**

```bash
git add base-env-build/matclaw-cips-gpu scripts/run_gpu_paper.sh eval.py \
  031-matclaw-cips-active-distillation/task.toml \
  032-matclaw-cips-curie-temperature/task.toml \
  033-matclaw-cips-domain-wall-search/task.toml \
  docs/gpu-execution.md tests/test_matclaw_case_contracts.py
git commit -m "feat: enforce MatClaw GPU execution identity"
```

### Task 7: Repair and Qualify Case 031 Active Distillation

**Files:**
- Modify: `031-matclaw-cips-active-distillation/solution/run_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/solution/alt_distillation.py`
- Modify: `031-matclaw-cips-active-distillation/solution/run_profiles.json`
- Modify: `031-matclaw-cips-active-distillation/tests/test_outputs.py`
- Modify: `031-matclaw-cips-active-distillation/tests/verifier.py`

**Interfaces:**
- Add `select_informative(deviation: np.ndarray, low: float, high: float, cap: int) -> np.ndarray`.
- Add `checkpoint(output: Path, state: dict) -> None` using atomic `*.tmp` then `Path.replace`.
- Paper result records `exploration_attempts`, `selected_configuration_hashes`, `training_frames_before`, and `training_frames_after` for each iteration.

- [ ] **Step 1: Add tests for a real active step**

Test selection-band inclusivity, no out-of-band fallback, exact data growth, train/test disjointness, two distinct committee models, atomic resume, and paper rejection when iteration 0 MAE is already below threshold but no selection/retrain occurred.

- [ ] **Step 2: Confirm the new tests fail**

Run: `pytest -q 031-matclaw-cips-active-distillation/tests`

Expected: at least the mandatory-active-step and checkpoint tests fail.

- [ ] **Step 3: Implement genuine exploration extension**

If an exploration batch has no frame in `[0.05, 0.15]`, run the next declared temperature/seed batch and append its raw trajectory; do not select nearest, duplicate, or fabricated frames. Stop only after the declared exploration-attempt cap, in which case the run is invalid. Even if iteration 0 MAE is `<0.10`, paper mode must complete one selection/relabel/retrain cycle before accepting convergence.

- [ ] **Step 4: Make state resumable and auditable**

After teacher MD, each committee model, each exploration trajectory, each labelled addition, and each retrain, atomically update `checkpoint.json` with hashes. Resume only when structure, model, profile, seed, and all completed artifact hashes match; otherwise fail rather than reuse incompatible state.

- [ ] **Step 5: Run CPU smoke and alternative implementation**

Run the primary and `alt_distillation.py` smoke paths with different seeds. Expected: both hidden-verifier reports are valid smoke results, have at least one selected frame, grow training data exactly, and do not claim formal validity.

- [ ] **Step 6: Run a GPU diagnostic paper qualification**

Use `evidence_class=diagnostic` and a new output path. Expected: one real active iteration completes, final recomputed MAE is `<0.10`, relative error from 0.098 is `<=0.25`, and all evidence hashes verify. If not, adjust only scientifically declared exploration temperatures, training steps, and committee seeds, repeat diagnostics, and keep every failed result diagnostic.

- [ ] **Step 7: Commit the qualified implementation and declared profile**

```bash
git add 031-matclaw-cips-active-distillation/solution 031-matclaw-cips-active-distillation/tests
git commit -m "fix: complete Case 031 active distillation loop"
```

### Task 8: Make Case 032 Complete, Resumable, and Source-Comparable

**Files:**
- Modify: `032-matclaw-cips-curie-temperature/solution/run_curie.py`
- Modify: `032-matclaw-cips-curie-temperature/solution/alt_curie.py`
- Modify: `032-matclaw-cips-curie-temperature/solution/run_profiles.json`
- Modify: `032-matclaw-cips-curie-temperature/tests/test_outputs.py`
- Modify: `032-matclaw-cips-curie-temperature/tests/test_analysis.py`
- Modify: `032-matclaw-cips-curie-temperature/tests/verifier.py`

**Interfaces:**
- Add `trajectory_contract(profile: dict, temperature_K: int, pilot: bool) -> dict`.
- Add `load_completed_temperature(path: Path, expected: dict) -> dict | None` that returns a record only after hash, frame, atom, timestep, seed, and finite-value checks.
- Paper result has separate `pilot` and `temperatures` records; `temperatures` keys exactly match the 13-value policy grid.

- [ ] **Step 1: Add exact protocol and resume tests**

Test 350 K pilot frame count, production frame counts, 360 atoms, exact grid, partial trajectory rejection, completed trajectory reuse, corrupted checkpoint rejection, periodic-z unwrapping, `Q(T)=mean(abs(eta))`, both Tc estimators, and source tolerance.

- [ ] **Step 2: Confirm failures**

Run: `pytest -q 032-matclaw-cips-curie-temperature/tests`

Expected: new resume and exact-grid tests fail until implemented.

- [ ] **Step 3: Implement atomic per-trajectory completion**

Write the pilot to `pilot_350K.partial.traj` and production runs to paths such as `T100K.partial.traj`; after validating exact expected frames and finite arrays, rename each to its `.traj` final name and atomically write the same stem with `.json`. On resume, reuse only completed pairs whose input identity and SHA-256 match. Never treat a partial trajectory as a production record.

- [ ] **Step 4: Separate simulation from independent analysis**

Generate `eta` and Tc claims in the solution, but keep verifier functions independent. Require the pilot before production; require all 13 production records before `formal_result=true`; calculate and store half-height, piecewise, combined Tc, and uncertainty.

- [ ] **Step 5: Run smoke, alternative, and diagnostic paper modes**

Expected smoke: verifier-valid and non-formal. Expected diagnostic paper: pilot plus all 13 trajectories, exact atom/frame counts, every trajectory converged, recomputed Tc within `251.3–271.3 K`. Any incomplete resumed run remains diagnostic.

- [ ] **Step 6: Commit**

```bash
git add 032-matclaw-cips-curie-temperature/solution 032-matclaw-cips-curie-temperature/tests
git commit -m "fix: complete Case 032 Curie workflow"
```

### Task 9: Make Case 033 Truly Sequential and Adaptive

**Files:**
- Modify: `033-matclaw-cips-domain-wall-search/solution/search_policy.py`
- Modify: `033-matclaw-cips-domain-wall-search/solution/run_search.py`
- Modify: `033-matclaw-cips-domain-wall-search/solution/alt_search.py`
- Modify: `033-matclaw-cips-domain-wall-search/tests/test_outputs.py`
- Modify: `033-matclaw-cips-domain-wall-search/tests/test_analysis.py`
- Modify: `033-matclaw-cips-domain-wall-search/tests/verifier.py`

**Interfaces:**
- Add `canonical_feedback(history: list[dict]) -> bytes`, serializing only completed measured jobs with sorted keys and compact separators.
- Add `feedback_sha256(history: list[dict]) -> str`.
- Each round after 1 records `decision_input_sha256`, `decision_made_at`, and `preceding_job_count` before starting its trajectories.

- [ ] **Step 1: Add chronological/adaptive tests**

Test rejection of precomputed fourteen-job history, round gaps, more than two jobs, round 2 without round 1 hashes, a decision timestamp preceding prior artifact completion, narrative-only `decision_basis`, and best values not derived from trajectories.

- [ ] **Step 2: Confirm failures**

Run: `pytest -q 033-matclaw-cips-domain-wall-search/tests`

Expected: chronological evidence tests fail against the current replay implementation.

- [ ] **Step 3: Execute one measured round at a time**

For each round: load and validate completed prior jobs, hash canonical feedback, call `propose_round`, atomically record the decision, run no more than two trajectories, analyze them, and only then advance the checkpoint. A restart may resume a completed job but cannot change a prior decision under the same run identity.

- [ ] **Step 4: Verify the electric-field protocol**

Keep `F_i=q_i E` and `U=-sum_i(q_i r_i dot E)` in `field_calculator.py`. Add finite-difference energy/force consistency plus CPU/GPU parity tests using the pinned charges. The verifier must derive the field protocol from actual calculator behavior and raw trajectories, not prompt prose.

- [ ] **Step 5: Run smoke, alternative, and diagnostic paper modes**

Expected diagnostic paper: seven chronological rounds, fourteen jobs, recomputed best condition in `Ez=[-0.18,-0.14]`, `T=[30,70]`, slope `>0.3`, and a valid digest chain from every decision to all preceding measurements.

- [ ] **Step 6: Commit**

```bash
git add 033-matclaw-cips-domain-wall-search/solution 033-matclaw-cips-domain-wall-search/tests
git commit -m "fix: make Case 033 search evidence-adaptive"
```

### Task 10: Close and Analyze Current Diagnostic Runs Without Promoting Them

**Files:**
- Modify: `evidence/matclaw/diagnostic/index.json`
- Create: `evidence/matclaw/diagnostic/summary.md`

**Interfaces:**
- Diagnostic summary reports completeness, verifier outcome, scientific metrics, wall time, failure reason, and formal ineligibility for each current run.

- [ ] **Step 1: Wait for existing containers; do not restart them**

Read their logs and filesystem at intervals no shorter than one minute. When each exits, record its actual exit code and final artifact counts. Do not copy any artifact into a formal run directory.

- [ ] **Step 2: Run current hidden verifiers read-only**

Run each verifier against its diagnostic workspace with the actual profile. Record failures exactly, including incomplete frames, stale code semantics, numerical misses, or missing results.

- [ ] **Step 3: Write the diagnostic conclusion**

`summary.md` must begin:

```markdown
# MatClaw diagnostic runs

These runs are pre-freeze diagnostics and are not eligible for formal validation.
```

Include Case 031's surviving `0.1248839088558909 eV/angstrom` miss and the final outcomes of the live 032/033 jobs.

- [ ] **Step 4: Commit manifests only**

```bash
git add evidence/matclaw/diagnostic/index.json evidence/matclaw/diagnostic/summary.md
git commit -m "docs: record MatClaw diagnostic outcomes"
```

### Task 11: Freeze Commit and Images, Then Execute Formal Pass 1

**Files:**
- Create locally: `evidence/matclaw/formal/031/run-1/manifest.json`, `evidence/matclaw/formal/032/run-1/manifest.json`, and `evidence/matclaw/formal/033/run-1/manifest.json`
- Modify after runs: generated case `VALIDATION.json` and `benchmark_valid.json`

**Interfaces:**
- Seeds: Case 031 `2026081101`, Case 032 `2026081201`, Case 033 `2026081301`.
- Each run has a unique empty output root and the same frozen Git/GPU/CPU image identities.

- [ ] **Step 1: Run the pre-freeze repository gate**

```bash
pytest -q
git diff --check
git status --short
```

Expected: all tests pass, diff check passes, and status is empty. Record `git rev-parse HEAD` as `FORMAL_COMMIT`; do not amend or commit after this point until both formal passes finish.

- [ ] **Step 2: Freeze immutable image identities**

Build the GPU execution and CPU verifier images from `FORMAL_COMMIT`, push them to the controlled registry if local Docker cannot produce stable RepoDigests, and record both `name@sha256:digest` values. Rerun qualification against the exact GPU digest. Expected: all five GPU gates pass.

- [ ] **Step 3: Execute formal 031 run 1**

Run the reference runner with paper profile, seed `2026081101`, formal evidence, the frozen GPU digest, and a new empty `run-1` directory. Then run the hidden verifier in the frozen CPU image and update the manifest with its full report and artifact hashes.

Expected: real active iteration count `>=1`, final recomputed MAE `<0.10`, source-relative error `<=0.25`.

- [ ] **Step 4: Execute formal 032 run 1**

Use seed `2026081201` and a new empty directory. Expected: complete pilot, all 13 temperatures, exact 360 atoms and frame counts, recomputed Tc in `251.3–271.3 K`.

- [ ] **Step 5: Execute formal 033 run 1**

Use seed `2026081301` and a new empty directory. Expected: seven sequential rounds, fourteen jobs, best region inside the specified bounds, recomputed slope `>0.3`.

- [ ] **Step 6: Derive the intermediate state**

Run:

```bash
python scripts/matclaw_validation.py derive --case all --evidence-root evidence/matclaw/formal --write
```

Expected: each case reaches `formal_run_1_valid` but all remain `benchmark_valid=false` because run 2 is absent.

### Task 12: Execute Independent Formal Pass 2

**Files:**
- Create locally: `evidence/matclaw/formal/031/run-2/manifest.json`, `evidence/matclaw/formal/032/run-2/manifest.json`, and `evidence/matclaw/formal/033/run-2/manifest.json`
- Modify after runs: generated case `VALIDATION.json` and `benchmark_valid.json`

**Interfaces:**
- Seeds: Case 031 `2026081102`, Case 032 `2026081202`, Case 033 `2026081302`.

- [ ] **Step 1: Prove source and images did not change**

Assert current HEAD equals `FORMAL_COMMIT`, worktree is clean, and resolved GPU/CPU digests equal pass 1. Expected: exact equality; otherwise discard the attempted formal pass and return to Task 11 after a new freeze.

- [ ] **Step 2: Prove storage isolation**

Create new empty run-2 roots. Before launch, assert no symlink or bind mount points into any run-1 directory and no checkpoint/model/trajectory/result exists in run 2.

- [ ] **Step 3: Run and verify 031 with seed `2026081102`**

Expected: all single-run Case 031 gates pass; do not yet accept cross-run agreement until Task 13 derives it.

- [ ] **Step 4: Run and verify 032 with seed `2026081202`**

Expected: all single-run Case 032 gates pass.

- [ ] **Step 5: Run and verify 033 with seed `2026081302`**

Expected: all single-run Case 033 gates pass.

- [ ] **Step 6: Derive statuses without manual edits**

Run the validation CLI. Expected: a case becomes true only if both manifests, raw bundles, verifier reports, source tolerance, and cross-run tolerance all pass. A failed case remains false with an exact reason and is rerun as a new attempt, never overwritten.

### Task 13: Final Comparison, Review, and Clean Integration

**Files:**
- Create: `evidence/matclaw/formal/comparison.json`
- Create: `evidence/matclaw/formal/README.md`
- Modify: generated `VALIDATION.json` and `benchmark_valid.json` for 031–033
- Modify: top-level MatClaw documentation only if exact commands changed

**Interfaces:**
- `comparison.json` contains per-case run IDs, seeds, commit, image digests, individual metrics, differences, thresholds, verifier status, and final derived boolean.

- [ ] **Step 1: Generate rather than hand-write the comparison**

Extend/use `matclaw_validation.py` to write `comparison.json`; `README.md` explains artifact-store location, retrieval, hash verification, and rerun commands. Do not commit raw trajectories or models.

- [ ] **Step 2: Run the complete verification matrix**

```bash
pytest -q tests/test_matclaw_source_recovery.py tests/test_matclaw_case_contracts.py tests/test_matclaw_validation.py
pytest -q 031-matclaw-cips-active-distillation/tests
pytest -q 032-matclaw-cips-curie-temperature/tests
pytest -q 033-matclaw-cips-domain-wall-search/tests
pytest -q
bash -n scripts/run_matclaw_reference.sh scripts/run_gpu_paper.sh
python -m json.tool evidence/matclaw/formal/comparison.json >/dev/null
git diff --check
```

Expected: every command exits 0 and all three generated `benchmark_valid.json` files are true. If any is false, report that case as not yet reproduced; do not override the generator.

- [ ] **Step 3: Perform evidence and code review**

Invoke `superpowers:requesting-code-review`. Review public isolation, hidden verifier independence, manifest path safety, GPU identity, run independence, exact source tolerances, and the absence of unrelated changes.

- [ ] **Step 4: Commit generated evidence summaries**

```bash
git add evidence/matclaw/formal/comparison.json evidence/matclaw/formal/README.md \
  031-matclaw-cips-active-distillation/{VALIDATION.json,benchmark_valid.json} \
  032-matclaw-cips-curie-temperature/{VALIDATION.json,benchmark_valid.json} \
  033-matclaw-cips-domain-wall-search/{VALIDATION.json,benchmark_valid.json}
git commit -m "evidence: validate MatClaw cases 031 through 033"
```

- [ ] **Step 5: Verify the final branch and hand off**

Run `git status --short`, `git log --oneline --decorate -15`, and the validation CLI once more without `--write`. Expected: clean branch, comparison unchanged, all three statuses true. Use `superpowers:finishing-a-development-branch` to present merge/PR/keep options; do not merge without the user's choice.

## Execution Checkpoints and Time Budget

1. Tasks 1–5: contract/evidence layer and CPU tests, approximately 4–8 hours.
2. Task 6: GPU build, compatibility, parity, and performance probe, approximately 2–6 hours.
3. Tasks 7–9: case repair plus diagnostic qualification, governed by observed failures; approximately 4–12 hours.
4. Task 10: current jobs finish independently and do not block code work.
5. Task 11: one formal pass of all cases, target 6–12 hours after the GPU probe.
6. Task 12: second independent pass, another 6–12 hours.
7. Task 13: comparison, review, and integration, approximately 2–4 hours.

Plan on 18–30 hours after the implementation is GPU-qualified. This range is scheduling guidance only; scientific gates, not elapsed time, determine success.
