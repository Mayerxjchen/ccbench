# MLIP Benchmark Infrastructure Refactor Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Ablation-Ready Infrastructure v0 for the 41-case benchmark, then extend it incrementally into Portable Infrastructure v1 without changing existing scientific tasks or prematurely running formal No-Skill/With-Skill experiments.

**Architecture:** Keep `eval.py` as a compatibility CLI while extracting a typed `dftworld_bench` package for contracts, allowlist packaging, lifecycle, quarantine, separate verification, result classification, and immutable run records. Local cases run compute inside the Candidate; Cases 031–034 use an optional `bench-hpc` client and trusted gateway that wraps the existing Slurm machinery without exposing SSH or scheduler credentials to the Candidate.

**Tech Stack:** Python 3.12+, dataclasses, `tomllib`, PyYAML 6, JSON Schema 2020-12, `jsonschema` 4, Docker CLI, pytest 8+, pagentv4, Apptainer, Slurm.

## Global Constraints

- Do not change case instructions, scientific references, thresholds, hidden fixtures, expert solutions, or the scientific meaning of Cases 031–034.
- Preserve the untracked `ai2kit/` tree in the main worktree.
- Do not modify or clean `/Users/chenxuanjie/案例测试/dftworld2/.worktrees/034-hpc-controller`; it contains active 034 evidence and untracked rerun scripts.
- Do not run formal No-Skill/With-Skill trials until Task 14 freezes an Ablation-Ready v0 release manifest.
- Pilot results use a distinct experiment ID and are never included in formal summaries.
- Keep the existing root-level `NNN-slug` case directories until Portable v1; do not combine infrastructure refactoring with a 41-directory rename.
- The canonical execution values are exactly `local_sandbox` and `hpc_controller`.
- The legacy value `real_hpc_controller` is accepted only through an explicit compatibility mapping and is recorded in provenance.
- Never infer execution class from a case number or directory name.
- Candidate bundles are constructed by positive allowlist; never copy a whole case and delete private paths.
- Candidate and Verifier never share a running container or writable workspace.
- The Candidate never receives a private key, SSH agent socket, raw site configuration, or scheduler command surface in the final v0 path.
- `INFRA_INVALID` observations are excluded from scientific success denominators.
- Every task ends with focused tests and a commit containing only that task's files.

## Delivery Sequence

```text
Phase A  Contracts and public packaging       Tasks 0–3
Phase B  Trusted Common Core                  Tasks 4–8
Phase C  Optional HPC Extension               Tasks 9–11
Phase D  Scientific migration and v0 freeze   Tasks 12–14
Phase E  Post-ablation Portable v1            Tasks 15–16
```

The first formal ablation is allowed after Task 14. Tasks 15–16 deliberately
follow the experiment so that cross-site and bulk-migration work does not delay
the first trustworthy result.

---

### Task 0: Promote the approved design into normative architecture documents

**Files:**
- Create: `docs/architecture/THREAT-MODEL.md`
- Create: `docs/architecture/LIFECYCLE.md`
- Create: `docs/architecture/RESULT-TAXONOMY.md`
- Create: `docs/architecture/CASE-STANDARD.md`
- Create: `docs/architecture/HPC-CONTRACT-v1.md`
- Create: `tests/docs/test_architecture_contracts.py`

**Interfaces:**
- Defines the normative meanings of Candidate, Harness, Gateway, Adapter,
  Compute Runtime, Quarantine, Verifier, and Evaluator.
- Freezes the execution enums, lifecycle states, result classes, HPC job states,
  trust assumptions, and compatibility rules used by later code.

- [ ] **Step 1: Write the failing architecture-consistency test**

```python
EXPECTED_EXECUTION_CLASSES = {"local_sandbox", "hpc_controller"}
EXPECTED_RESULT_CLASSES = {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}
EXPECTED_JOB_STATES = {
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED",
    "CANCELLED", "TIMEOUT", "LOST",
}


def test_normative_documents_define_the_same_enums():
    assert execution_classes_from_docs() == EXPECTED_EXECUTION_CLASSES
    assert result_classes_from_docs() == EXPECTED_RESULT_CLASSES
    assert job_states_from_docs() == EXPECTED_JOB_STATES
```

Also assert the threat model says the Candidate and submission are untrusted,
the lifecycle destroys Candidate before Verifier start, and the HPC contract
forbids Candidate access to raw SSH/scheduler credentials.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/docs/test_architecture_contracts.py -q`

Expected: FAIL because the normative documents do not exist.

- [ ] **Step 3: Write the five documents from the approved design baseline**

`THREAT-MODEL.md` separates Common threats T1–T7 from HPC threats H1–H6 and
states trust assumptions. `LIFECYCLE.md` defines legal transitions, cleanup,
deadlines, and recovery. `RESULT-TAXONOMY.md` owns classification and retry
semantics. `CASE-STANDARD.md` owns allowlist packaging, legacy layout, case
versioning, and digest rules. `HPC-CONTRACT-v1.md` owns the seven commands,
job states, idempotency, settlement, accounting, and workspace isolation.

- [ ] **Step 4: Verify GREEN and internal consistency**

Run: `.venv/bin/pytest tests/docs/test_architecture_contracts.py -q`

Expected: all normative enums and security assertions match.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture tests/docs/test_architecture_contracts.py
git commit -m "docs: freeze benchmark infrastructure v0 contracts"
```

---

### Task 1: Create the package boundary and schema toolchain

**Files:**
- Modify: `pyproject.toml`
- Create: `dftworld_bench/__init__.py`
- Create: `dftworld_bench/contracts/__init__.py`
- Create: `dftworld_bench/core/__init__.py`
- Create: `dftworld_bench/hpc/__init__.py`
- Create: `tests/test_package_imports.py`

**Interfaces:**
- Produces importable packages `dftworld_bench.contracts`, `dftworld_bench.core`, and `dftworld_bench.hpc`.
- Adds runtime dependencies `PyYAML>=6.0.2,<7` and `jsonschema>=4.23,<5`.

- [ ] **Step 1: Write the failing package import test**

```python
def test_infrastructure_packages_are_importable():
    import dftworld_bench.contracts
    import dftworld_bench.core
    import dftworld_bench.hpc
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_package_imports.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'dftworld_bench'`.

- [ ] **Step 3: Add the package files and dependencies**

Add to `[project].dependencies`:

```toml
"PyYAML>=6.0.2,<7",
"jsonschema>=4.23,<5",
```

Each new `__init__.py` contains only a module docstring; no runtime behavior is
introduced in this task.

- [ ] **Step 4: Sync and verify GREEN**

Run: `uv sync`

Run: `.venv/bin/pytest tests/test_package_imports.py -q`

Expected: one passing test.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock dftworld_bench tests/test_package_imports.py
git commit -m "infra: create benchmark core package boundary"
```

---

### Task 2: Define and load the versioned Case Contract

**Files:**
- Create: `schemas/case.schema.json`
- Create: `dftworld_bench/contracts/case.py`
- Create: `tests/contracts/test_case_contract.py`
- Modify: `001-hello/task.toml`
- Modify: `009-cp2k-run/task.toml`
- Modify: `031-matclaw-cips-active-distillation/task.toml`
- Modify: `032-matclaw-cips-curie-temperature/task.toml`
- Modify: `033-matclaw-cips-domain-wall-search/task.toml`
- Modify: `034-ai2kit-water64-end-to-end-potential/task.toml`

**Interfaces:**
- Produces `ExecutionClass = Literal["local_sandbox", "hpc_controller"]`.
- Produces `CaseSpec.load(case_dir: Path) -> CaseSpec`.
- `CaseSpec` fields: `case_id`, `case_version`, `schema_version`, `execution_class`, `instruction_path`, `public_files`, `submission_root`, `candidate_image`, `agent_timeout_sec`, `verifier_timeout_sec`, `verifier_env`, `candidate_resources`, `legacy_execution_value`, `legacy_submission_layout`.
- `PublicFileRule` fields: `source`, `destination`, and `strip_prefix`.
- Raises `CaseContractError` on ambiguity, invalid paths, unknown execution values, or conditional-schema violations.

- [ ] **Step 1: Write failing loader tests**

Cover these exact behaviors:

```python
def test_legacy_execution_value_is_normalized_and_recorded(tmp_path):
    case = make_case(tmp_path, execution_backend="real_hpc_controller")
    spec = CaseSpec.load(case)
    assert spec.execution_class == "hpc_controller"
    assert spec.legacy_execution_value == "real_hpc_controller"


def test_execution_is_never_inferred_from_case_number(tmp_path):
    case = make_case(tmp_path, name="031-example", execution=None)
    with pytest.raises(CaseContractError, match="execution.class"):
        CaseSpec.load(case)


def test_both_toml_and_yaml_fail_closed(tmp_path):
    case = make_case(tmp_path, execution="local_sandbox")
    (case / "task.yaml").write_text("schema_version: 2\n")
    with pytest.raises(CaseContractError, match="both task.toml and task.yaml"):
        CaseSpec.load(case)
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/contracts/test_case_contract.py -q`

Expected: collection failure because `CaseSpec` does not exist.

- [ ] **Step 3: Implement the typed loader and JSON Schema validation**

The loader uses `tomllib` for `task.toml`, `yaml.safe_load` for `task.yaml`, and
`jsonschema.Draft202012Validator`. Normalize only this explicit alias:

```python
EXECUTION_ALIASES = {"real_hpc_controller": "hpc_controller"}
EXECUTION_CLASSES = frozenset({"local_sandbox", "hpc_controller"})
```

Reject absolute or parent-traversing public paths using `PurePosixPath`.
If `execution.class == "hpc_controller"`, require an `hpc` object; reject that
object for `local_sandbox` in canonical schema v2 documents.

- [ ] **Step 4: Add explicit execution declarations to the six reference manifests**

Use these exact TOML blocks:

```toml
[execution]
class = "local_sandbox"

[candidate]
instruction = "instruction.md"
submission_root = "."
legacy_submission_layout = true
```

for 001. For 009 add this explicit path-preserving rule:

```toml
[[candidate.files]]
source = "environment/H2O.inp"
destination = "H2O.inp"
```

For 031–034 use:

```toml
[execution]
class = "hpc_controller"

[candidate]
instruction = "instruction.md"
submission_root = "."
legacy_submission_layout = true

[[candidate.files]]
source = "public/**"
destination = "."
strip_prefix = "public"

[hpc]
contract_version = "hpc-execution/v1"
required_capabilities = ["batch_jobs", "gpu", "artifact_fetch"]
```

Remove 034's legacy `[task].execution_backend` only after the alias test exists.

- [ ] **Step 5: Verify all six manifests**

Run: `.venv/bin/pytest tests/contracts/test_case_contract.py tests/test_matclaw_case_contracts.py -q`

Expected: all tests pass and the four HPC cases resolve explicitly to
`hpc_controller`.

- [ ] **Step 6: Commit**

```bash
git add schemas/case.schema.json dftworld_bench/contracts/case.py tests/contracts/test_case_contract.py 001-hello/task.toml 009-cp2k-run/task.toml 031-matclaw-cips-active-distillation/task.toml 032-matclaw-cips-curie-temperature/task.toml 033-matclaw-cips-domain-wall-search/task.toml 034-ai2kit-water64-end-to-end-potential/task.toml
git commit -m "infra: define versioned dual-mode case contract"
```

---

### Task 3: Replace Dockerfile-derived staging with an allowlist Case Packager

**Files:**
- Create: `schemas/bundle-manifest.schema.json`
- Create: `dftworld_bench/core/digests.py`
- Create: `dftworld_bench/core/packager.py`
- Create: `tests/core/test_packager.py`
- Modify: `tests/test_workspace_isolation.py`

**Interfaces:**
- Produces `sha256_file(path: Path) -> str`.
- Produces `package_candidate(spec: CaseSpec, destination: Path) -> BundleManifest`.
- `BundleManifest` contains `case_id`, `case_version`, `schema_version`, `files`, `public_digest`, and `leak_scan`.
- Each file record contains normalized `path`, byte `size`, and lowercase SHA-256.

- [ ] **Step 1: Write failing deterministic-bundle and leakage tests**

```python
def test_packager_is_allowlist_only_and_deterministic(case_fixture, tmp_path):
    first = package_candidate(case_fixture, tmp_path / "first")
    second = package_candidate(case_fixture, tmp_path / "second")
    assert first.public_digest == second.public_digest
    assert not (tmp_path / "first" / "reference").exists()
    assert not (tmp_path / "first" / "solution").exists()
    assert not (tmp_path / "first" / "tests").exists()


def test_packager_rejects_symlink_in_public(case_fixture, tmp_path):
    (case_fixture.path / "public" / "escape").symlink_to("../reference")
    with pytest.raises(PackageError, match="symlink"):
        package_candidate(case_fixture, tmp_path / "bundle")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_packager.py -q`

Expected: import failure for `dftworld_bench.core.packager`.

- [ ] **Step 3: Implement positive-only copying**

Resolve every source glob under the case root, reject symlinks and non-regular
files, apply the declared `strip_prefix` and `destination`, reject destination
collisions, sort normalized destinations, copy instruction to `instruction.md`,
and write `bundle-manifest.json` last. Compute the root digest
over lines of the form:

```text
<sha256>  <size>  <normalized-relative-path>\n
```

The forbidden-name leak scan includes `reference`, `solution`, `tests`,
`verifier`, `thresholds`, `fixtures`, `.git`, and `expert_notes`.

- [ ] **Step 4: Convert workspace-isolation tests to exercise the packager**

Keep legacy Docker COPY parser tests as compatibility tests, but make the new
packager tests the release gate. Add an assertion that no root-level case
directory is mounted or copied wholesale.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/pytest tests/core/test_packager.py tests/test_workspace_isolation.py -q`

Expected: deterministic manifests and zero hidden-file leaks.

- [ ] **Step 6: Commit**

```bash
git add schemas/bundle-manifest.schema.json dftworld_bench/core/digests.py dftworld_bench/core/packager.py tests/core/test_packager.py tests/test_workspace_isolation.py
git commit -m "infra: package candidate inputs from an explicit allowlist"
```

---

### Task 4: Introduce the lifecycle state machine and result taxonomy

**Files:**
- Create: `schemas/result.schema.json`
- Create: `dftworld_bench/contracts/result.py`
- Create: `dftworld_bench/core/lifecycle.py`
- Create: `tests/core/test_lifecycle.py`
- Create: `tests/contracts/test_result_contract.py`

**Interfaces:**
- Produces `RunPhase` enum with the states from the approved design.
- Produces `Lifecycle.transition(next_phase: RunPhase, at: datetime) -> None`.
- Produces `ResultClass`, `FailureCode`, and `BenchmarkResult`.
- `BenchmarkResult.is_counted_scientifically` is true only for `VALID_RESULT`.

- [ ] **Step 1: Write failing transition and classification tests**

```python
def test_verifier_cannot_start_before_candidate_is_destroyed():
    lifecycle = Lifecycle(run_id="r1")
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.VERIFYING, NOW)


def test_infra_invalid_is_not_a_scientific_failure():
    result = BenchmarkResult.infra_invalid("r1", FailureCode.HPC_FAILURE, "node lost")
    assert result.result_class == ResultClass.INFRA_INVALID
    assert result.is_counted_scientifically is False
    assert result.retryable is True
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_lifecycle.py tests/contracts/test_result_contract.py -q`

Expected: missing lifecycle and result modules.

- [ ] **Step 3: Implement explicit legal transitions and JSON serialization**

Store each transition as an immutable event containing `phase`, UTC timestamp,
and optional reason. Reject backward transitions and direct jumps. Define the
failure codes exactly as the design baseline; do not collapse exceptions into
`reward = 0`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/core/test_lifecycle.py tests/contracts/test_result_contract.py -q`

Expected: all legal, illegal, and retryability cases pass.

- [ ] **Step 5: Commit**

```bash
git add schemas/result.schema.json dftworld_bench/contracts/result.py dftworld_bench/core/lifecycle.py tests/core/test_lifecycle.py tests/contracts/test_result_contract.py
git commit -m "infra: model trusted lifecycle and benchmark outcomes"
```

---

### Task 5: Implement Artifact Quarantine and submission sealing

**Files:**
- Create: `schemas/submission.schema.json`
- Create: `dftworld_bench/core/quarantine.py`
- Create: `tests/core/test_quarantine.py`

**Interfaces:**
- Produces `QuarantineLimits(max_files, max_single_bytes, max_total_bytes, allow_archives=False)`.
- Produces `collect_raw_submission(workspace: Path, submission_root: PurePosixPath, raw: Path, legacy_layout: bool) -> None`.
- Produces `quarantine_submission(raw: Path, clean: Path, limits: QuarantineLimits) -> SubmissionSeal`.
- `SubmissionSeal` contains normalized file records, total bytes, manifest digest, and seal timestamp.

- [ ] **Step 1: Write failing hostile-filesystem tests**

Tests must reject symlink, hardlink count greater than one, FIFO, socket, device,
setuid/setgid bits, parent traversal in `manifest.json`, excessive file count,
single-file limit, aggregate limit, and every archive extension when
`allow_archives=False`.

```python
def test_quarantine_rejects_fifo(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    os.mkfifo(raw / "pipe")
    with pytest.raises(QuarantineError, match="FIFO"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_quarantine.py -q`

Expected: missing quarantine module.

- [ ] **Step 3: Implement lstat-based validation and copy**

For canonical cases, collect only `final/`. For legacy root-layout cases,
collect the workspace while excluding exactly `.venv`, `.skills`,
`_dftworld_tests`, `tests`, `reference`, `solution`, `.pytest_cache`, and
`logs`; record the exclusion list in the seal evidence. Walk without following
links, reject unsafe nodes before copying, normalize
permissions to directories `0755` and files `0644`, copy into a new private
directory, fsync the manifest, and atomically rename it to the sealed path.
Never import Candidate Python or load pickle during quarantine.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/core/test_quarantine.py -q`

Expected: safe trees seal deterministically; every hostile fixture fails closed.

- [ ] **Step 5: Commit**

```bash
git add schemas/submission.schema.json dftworld_bench/core/quarantine.py tests/core/test_quarantine.py
git commit -m "infra: quarantine and seal untrusted submissions"
```

---

### Task 6: Run every Verifier in a fresh isolated container

**Files:**
- Create: `dftworld_bench/core/verifier.py`
- Create: `tests/core/test_verifier_runner.py`
- Modify: `eval.py`
- Modify: `README.md`

**Interfaces:**
- Produces `VerifierSpec(image, timeout_sec, env, tests_dir, reference_dir)`.
- Produces `build_verifier_command(spec, submission, logs) -> list[str]`.
- Produces `run_verifier(spec, sealed_submission, logs) -> BenchmarkResult`.
- Removes the Candidate-container `verify(sandbox, task_dir, timeout)` path from `eval.py`.

- [ ] **Step 1: Write failing verifier-boundary tests**

Assert the command uses `docker run --rm`, `--network none`, non-root user
`65532:65532`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, a private
`/tmp` tmpfs, read-only `/submission`, `/tests`, and `/reference`, plus a
writable `/logs/verifier` mount. Assert it does not contain the Candidate
container ID, Candidate workspace, Docker socket, or SSH material.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_verifier_runner.py -q`

Expected: missing `dftworld_bench.core.verifier`.

- [ ] **Step 3: Implement the isolated command and strict result parser**

The verifier must write `/logs/verifier/result.json` conforming to
`schemas/result.schema.json`. Missing, malformed, or schema-invalid output maps
to `INFRA_INVALID/VERIFIER_FAILURE`, not `SCIENTIFIC_FAIL`.

- [ ] **Step 4: Rewire `eval.py` lifecycle order**

After the Agent turn: stop writes, collect the declared submission into a
private raw directory, close the Candidate, quarantine the raw directory, then
invoke `run_verifier`. Mount the sealed submission read-only at both
`/submission` and legacy-compatible `/app`; this preserves existing verifier
paths without sharing the Candidate workspace. Remove staging of `_dftworld_tests` into
the Candidate workspace and remove scientific verification through
`docker exec`.

- [ ] **Step 5: Verify focused and legacy gates**

Run: `.venv/bin/pytest tests/core/test_verifier_runner.py tests/test_workspace_isolation.py tests/test_chemgraph_case_contracts.py -q`

Expected: Candidate destruction precedes Verifier start and existing scientific
fixtures retain their prior results.

- [ ] **Step 6: Commit**

```bash
git add dftworld_bench/core/verifier.py tests/core/test_verifier_runner.py eval.py README.md
git commit -m "infra: verify sealed submissions outside the candidate"
```

---

### Task 7: Write append-only immutable Run Records

**Files:**
- Create: `schemas/run-record.schema.json`
- Create: `dftworld_bench/contracts/run_record.py`
- Create: `dftworld_bench/core/run_store.py`
- Create: `tests/core/test_run_store.py`
- Modify: `summarize.py`

**Interfaces:**
- Produces `RunRecord` with case, execution, Agent/model, treatment, image,
  profile, skill, bundle, submission, verifier, platform, job, usage, lifecycle,
  and result identities.
- Produces `RunStore.create(record) -> Path`; an existing `run_id` raises
  `RunAlreadyExists`.
- The canonical record is `jobs/<run_id>/run-record.json`; `summary.json`
  remains a derived compatibility view.

- [ ] **Step 1: Write failing immutability and digest tests**

```python
def test_run_store_never_overwrites(tmp_path, valid_record):
    store = RunStore(tmp_path)
    store.create(valid_record)
    with pytest.raises(RunAlreadyExists):
        store.create(valid_record)


def test_run_record_requires_frozen_treatment_identity(valid_record):
    valid_record.condition_id = "with-skill"
    valid_record.skills_source = "image"
    valid_record.skills_sha = None
    with pytest.raises(RunRecordError, match="skills_sha"):
        valid_record.validate()
```

For `no-skill`, require `skills_source == "none"` and `skills_sha is None`;
for `with-skill`, require an immutable image identity and content digest.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_run_store.py -q`

Expected: missing run record/store modules.

- [ ] **Step 3: Implement atomic append-only persistence**

Write to `run-record.json.tmp`, fsync, validate against schema, then rename.
Refuse updates. Store secret-free site-config digest rather than the config
contents. Record legacy execution normalization when it occurred.

- [ ] **Step 4: Make `summarize.py` prefer run records**

Read `run-record.json` when present and fall back to legacy `summary.json`.
Exclude `INFRA_INVALID` from success denominators and display it in a separate
infrastructure-invalid count.

- [ ] **Step 5: Verify GREEN and compatibility**

Run: `.venv/bin/pytest tests/core/test_run_store.py tests/test_summarize.py -q`

Expected: immutable writes pass and old summaries still render.

- [ ] **Step 6: Commit**

```bash
git add schemas/run-record.schema.json dftworld_bench/contracts/run_record.py dftworld_bench/core/run_store.py tests/core/test_run_store.py summarize.py tests/test_summarize.py
git commit -m "infra: persist immutable benchmark run records"
```

---

### Task 8: Extract the Common Harness and validate two Local reference cases

**Files:**
- Create: `dftworld_bench/core/harness.py`
- Create: `dftworld_bench/agents.py`
- Create: `tests/core/test_harness.py`
- Modify: `eval.py`
- Modify: `001-hello/tests/test.sh`
- Modify: `009-cp2k-run/tests/test.sh`

**Interfaces:**
- Produces `AgentAdapter` protocol: `prepare`, `start`, `stop`, `collect_logs`, `version`.
- Produces `PagentAdapter` wrapping the existing pagentv4 `Runner` behavior.
- Produces `TrustedHarness.run(spec, treatment, profile) -> BenchmarkResult`.
- `eval.py` becomes argument parsing plus calls into `TrustedHarness`.

- [ ] **Step 1: Write failing orchestration-order tests**

Use fakes to assert this exact Local order:

```python
assert events == [
    "package", "candidate_start", "agent_start", "agent_stop",
    "candidate_freeze", "submission_collect", "candidate_destroy",
    "quarantine", "verifier_start", "record_write",
]
```

Assert teardown is attempted after timeout and after Agent adapter exceptions.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/core/test_harness.py -q`

Expected: missing harness and adapter interfaces.

- [ ] **Step 3: Move orchestration without changing provider behavior**

Move task loading, runner opening, token/tool accounting, skill injection, and
result creation behind focused interfaces. Keep CLI flags and old output paths
compatible. Do not move scientific logic into the harness.

- [ ] **Step 4: Update the two reference verifier scripts to emit `result.json`**

Each script writes a schema-valid common result plus its existing CTRF evidence.
001 checks exact file content; 009 preserves its CP2K energy/output checks.

- [ ] **Step 5: Run architecture and end-to-end Local gates**

Run: `.venv/bin/pytest tests/core tests/contracts tests/test_workspace_isolation.py tests/test_summarize.py -q`

Run when images exist:

```bash
.venv/bin/python eval.py 001-hello --no-skills --experiment infra-v0-local-smoke
.venv/bin/python eval.py 009-cp2k-run --no-skills --experiment infra-v0-local-science-smoke
```

Expected: both produce `run-record.json`, sealed-submission metadata, independent
Verifier evidence, and no hidden assets in Candidate workspace.

- [ ] **Step 6: Commit**

```bash
git add dftworld_bench/core/harness.py dftworld_bench/agents.py tests/core/test_harness.py eval.py 001-hello/tests/test.sh 009-cp2k-run/tests/test.sh
git commit -m "infra: run local cases through the trusted common harness"
```

---

### Task 9: Define the stable HPC Job Contract and `bench-hpc` client

**Files:**
- Create: `schemas/hpc-job.schema.json`
- Create: `schemas/resource-profile.schema.json`
- Create: `schemas/platform-profile.schema.json`
- Create: `schemas/site-config.schema.json`
- Create: `dftworld_bench/hpc/job.py`
- Create: `dftworld_bench/hpc/client.py`
- Create: `dftworld_bench/hpc/__main__.py`
- Create: `tests/hpc/test_job_contract.py`
- Create: `tests/hpc/test_client.py`

**Interfaces:**
- Produces `JobSpec.load(path) -> JobSpec` with command argv, resources, inputs,
  outputs, environment, and idempotency key.
- Produces CLI commands `capabilities`, `submit`, `status`, `logs`, `fetch`,
  `cancel`, and `usage`.
- Client communicates only with `BENCH_HPC_GATEWAY_URL` and
  `BENCH_HPC_RUN_TOKEN`; it contains no SSH or scheduler implementation.

- [ ] **Step 1: Write failing conditional-schema and CLI tests**

Reject shell strings, absolute input/output paths, `..`, unknown resources,
walltime outside profile, missing idempotency key, and output globs escaping the
job workspace. Verify each CLI command emits stable JSON to stdout and errors
to stderr.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/hpc/test_job_contract.py tests/hpc/test_client.py -q`

Expected: missing job/client modules.

- [ ] **Step 3: Implement the data model and HTTP client**

Commands are always arrays, for example:

```yaml
schema_version: 1
idempotency_key: geopt-001
runtime: mlip-compute@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
command: [cp2k, -i, input.inp, -o, output.out]
resources:
  cpus: 64
  memory_gb: 128
  gpus: 0
  walltime_minutes: 240
inputs: [input.inp]
outputs: [output.out]
```

Use JSON request/response bodies and bearer authentication; never interpolate
job values into a local shell command.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/hpc/test_job_contract.py tests/hpc/test_client.py -q`

Expected: schema and all seven commands pass against a fake HTTP transport.

- [ ] **Step 5: Commit**

```bash
git add schemas/hpc-job.schema.json schemas/resource-profile.schema.json schemas/platform-profile.schema.json schemas/site-config.schema.json dftworld_bench/hpc/job.py dftworld_bench/hpc/client.py dftworld_bench/hpc/__main__.py tests/hpc/test_job_contract.py tests/hpc/test_client.py
git commit -m "infra: define the stable bench-hpc job interface"
```

---

### Task 10: Build a trusted gateway and process-test adapter

**Files:**
- Create: `dftworld_bench/hpc/adapters/__init__.py`
- Create: `dftworld_bench/hpc/adapters/base.py`
- Create: `dftworld_bench/hpc/adapters/process_test.py`
- Create: `dftworld_bench/hpc/gateway.py`
- Create: `dftworld_bench/hpc/gateway_runtime.py`
- Create: `dftworld_bench/hpc/conformance.py`
- Create: `runtimes/hpc-gateway/Dockerfile`
- Create: `tests/hpc/test_gateway.py`
- Create: `tests/hpc/test_gateway_runtime.py`
- Create: `tests/hpc/test_process_conformance.py`

**Interfaces:**
- Produces `HpcAdapter` protocol with all seven stable operations.
- Produces `Gateway.authorize(token, run_id, operation) -> Capability`.
- Produces `GatewayRuntime.start(run_id, adapter_config) -> GatewayLease` and
  `GatewayLease.close() -> None`.
- Produces `ProcessTestAdapter` for contract CI only; Local benchmark cases never use it.
- Produces `run_conformance(adapter) -> ConformanceReport`.

- [ ] **Step 1: Write failing trust-boundary tests**

Test token/run mismatch, expired token, operation outside scope, path access to
another run, duplicate idempotency key, quota excess, cancel after terminal
state, and fetch before success. Assert duplicate submit returns the original
job rather than launching twice.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/hpc/test_gateway.py tests/hpc/test_gateway_runtime.py tests/hpc/test_process_conformance.py -q`

Expected: missing gateway, runtime, and adapters.

- [ ] **Step 3: Implement the process state machine**

Use states `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMEOUT`,
and `LOST`. Store jobs under an adapter-private `run_id` directory with `0700`
permissions. The gateway, not the Candidate, owns the adapter object and quota
ledger.

- [ ] **Step 4: Implement the conformance report**

The suite exercises capabilities, upload/submit, status, logs, fetch, cancel,
idempotency, run isolation, resource accounting, and settlement. Emit a
machine-readable report with `conformant: true` only when every mandatory check
passes.

- [ ] **Step 5: Isolate Candidate networking from gateway egress**

For each HPC run, create a per-run Docker `--internal` network. Attach Candidate
only to that network. Attach the trusted gateway container to the internal
network and a separate egress-capable network. Mount SSH agent/socket and site
configuration only into the gateway container. Give Candidate only the gateway
URL and a run-scoped bearer token. `GatewayLease.close()` revokes the token,
stops the gateway, and removes both networks idempotently.

- [ ] **Step 6: Verify GREEN**

Run: `.venv/bin/pytest tests/hpc/test_gateway.py tests/hpc/test_gateway_runtime.py tests/hpc/test_process_conformance.py -q`

Expected: all contract checks pass without SSH or Slurm; Candidate cannot reach
an external HTTP fixture while gateway can reach its fake site endpoint.

- [ ] **Step 7: Commit**

```bash
git add dftworld_bench/hpc/adapters dftworld_bench/hpc/gateway.py dftworld_bench/hpc/gateway_runtime.py dftworld_bench/hpc/conformance.py runtimes/hpc-gateway/Dockerfile tests/hpc/test_gateway.py tests/hpc/test_gateway_runtime.py tests/hpc/test_process_conformance.py
git commit -m "infra: enforce run-scoped HPC capabilities in a trusted gateway"
```

---

### Task 11: Wrap the existing Slurm transport behind the adapter contract

**Files:**
- Create: `dftworld_bench/hpc/adapters/slurm.py`
- Create: `tests/hpc/test_slurm_adapter.py`
- Modify: `scripts/ablation/transport/slurm_transport.py`
- Modify: `scripts/matclaw_hpc_controller.py`
- Modify: `tests/test_matclaw_hpc_controller.py`
- Modify: `base-env-build/matclaw-cips-controller/Dockerfile`
- Create: `base-env-build/ai2kit-controller/Dockerfile`
- Modify: `base-env-build/build.sh`
- Create: `site-configs/example-slurm.yaml`

**Interfaces:**
- Produces `SlurmAdapter(site_config, transport, resource_profile, platform_profile)`.
- Existing `SshSlurmTransport` remains temporarily usable only inside the trusted gateway.
- Candidate-facing code contains no imports from `slurm_transport`, `paramiko`, or `subprocess ssh`.

- [ ] **Step 1: Write failing adapter translation tests**

Given a `JobSpec`, assert generated Slurm scripts take partition/account only
from validated site config; use `--gres=gpu:1` for the current <site-alias> profile;
use an explicit run workspace; and launch the frozen runtime with containment,
clean environment, no home bind, and only declared input/output paths.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/hpc/test_slurm_adapter.py -q`

Expected: missing Slurm adapter.

- [ ] **Step 3: Implement translation with argv-safe serialization**

Generate scheduler directives from typed values. Serialize the scientific argv
with `shlex.join` only after validating each element and never accept raw
`#SBATCH` lines from the Candidate. Namespace remote paths as:

```text
<workspace_root>/<case_id>/<run_id>/<job_id>/
```

- [ ] **Step 4: Put the old MatClaw controller behind the new adapter seam**

Keep its case policies, runtime locks, validation, and scientific behavior.
Replace direct Candidate SSH exposure with gateway-owned transport calls. Add a
source regression asserting `controller_docker_args` no longer mounts
`SSH_AUTH_SOCK`, `~/.ssh/config`, or `known_hosts` into Candidate containers.

Build both controller images with only the `bench-hpc` client surface exposed to
the Agent. OpenSSH and rsync belong in `runtimes/hpc-gateway`, not the Candidate
image. Add `ai2kit-controller` to `base-env-build/build.sh` so 034 no longer
depends on an undocumented external `dftworld-base-ai2kit:0.1.0-cpu-controller`
build.

- [ ] **Step 5: Run old and new suites**

Run: `.venv/bin/pytest tests/hpc/test_slurm_adapter.py tests/test_matclaw_hpc_controller.py tests/test_matclaw_runtime_lock.py -q`

Expected: legacy scientific/controller tests and new security/contract tests all pass.

- [ ] **Step 6: Run read-only site conformance**

Run first with a fake transport. Then run `capabilities` and a minimal scheduler
smoke through the approved SSH target without submitting a scientific job.
Record the report outside the Candidate bundle and verify no secret appears in it.

- [ ] **Step 7: Commit**

```bash
git add dftworld_bench/hpc/adapters/slurm.py tests/hpc/test_slurm_adapter.py scripts/ablation/transport/slurm_transport.py scripts/matclaw_hpc_controller.py tests/test_matclaw_hpc_controller.py base-env-build/matclaw-cips-controller/Dockerfile base-env-build/ai2kit-controller/Dockerfile base-env-build/build.sh site-configs/example-slurm.yaml
git commit -m "infra: adapt trusted Slurm execution to the bench-hpc contract"
```

---

### Task 12: Migrate Case 034 as the HPC scientific reference

**Files:**
- Create: `034-ai2kit-water64-end-to-end-potential/profiles/resource.yaml`
- Create: `034-ai2kit-water64-end-to-end-potential/profiles/platform.yaml`
- Create: `034-ai2kit-water64-end-to-end-potential/profiles/smoke.yaml`
- Create: `034-ai2kit-water64-end-to-end-potential/profiles/formal.yaml`
- Create: `034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json`
- Modify: `034-ai2kit-water64-end-to-end-potential/task.toml`
- Modify: `034-ai2kit-water64-end-to-end-potential/tests/test.sh`
- Create: `tests/hpc/test_034_integration.py`

**Interfaces:**
- 034 declares frozen CP2K, DeePMD, LAMMPS, basis/potential, resource, and platform identities.
- Its verifier emits common `result.json` while retaining L1–L9 scientific evidence.
- The formal profile remains more demanding than smoke; smoke success is never labeled scientific success.

- [ ] **Step 1: Write failing profile-lock tests**

Require a digest for every runtime artifact, exact CP2K 2023.2 data-asset hashes
already recorded in memory, GPU count 1, profile-specific DFT label/MD step
limits, and a formal verifier timeout of 10800 seconds.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/hpc/test_034_integration.py -q`

Expected: missing profiles/runtime lock.

- [ ] **Step 3: Add the frozen profiles and asset identities**

Record these known CP2K data hashes verbatim:

```text
GTH_BASIS_SETS  76d1ccd5204390abfcadeb5c0d5e609ff3d151c1c1a583a02349af348ef919f7
GTH_POTENTIALS  a4307115bacdaa253e0faa24b3f9f7ba7c1dc60585cbd83691c65ff51d0db9fd
dftd3.dat       1f5041914fb3a7fa6c97602d82da6cb9129c1c1dba675db23522de9c2837dafd
```

Copy them into each new remote run directory and re-hash before CP2K starts;
do not reference the module's broken compile-time `/tmp/hehr/cp2k-2023.2/data` path.

- [ ] **Step 4: Validate fixtures and process-test integration**

Run: `.venv/bin/pytest tests/hpc/test_034_integration.py 034-ai2kit-water64-end-to-end-potential/tests -q`

Expected: positive fixture passes, negative fixtures fail for their intended
scientific reasons, alternative-valid fixture passes, and no HPC job is needed.

- [ ] **Step 5: Run one formal reference through the gateway**

Use a new `run_id`, frozen inputs, and the approved site profile. Do not reuse
or overwrite the active 034 worktree evidence. Accept the run only if all jobs
settle, all declared artifacts fetch with matching hashes, and the independent
Verifier produces a valid common result.

- [ ] **Step 6: Commit source and non-secret validation metadata**

```bash
git add 034-ai2kit-water64-end-to-end-potential/profiles 034-ai2kit-water64-end-to-end-potential/reference/compute-runtime.lock.json 034-ai2kit-water64-end-to-end-potential/task.toml 034-ai2kit-water64-end-to-end-potential/tests/test.sh tests/hpc/test_034_integration.py
git commit -m "case(034): migrate water64 to the trusted HPC contract"
```

---

### Task 13: Migrate 032, 031, and 033 without changing their science

**Files:**
- Create: `031-matclaw-cips-active-distillation/profiles/resource.yaml`
- Create: `031-matclaw-cips-active-distillation/profiles/platform.yaml`
- Create: `031-matclaw-cips-active-distillation/profiles/smoke.yaml`
- Create: `031-matclaw-cips-active-distillation/profiles/formal.yaml`
- Create: `032-matclaw-cips-curie-temperature/profiles/resource.yaml`
- Create: `032-matclaw-cips-curie-temperature/profiles/platform.yaml`
- Create: `032-matclaw-cips-curie-temperature/profiles/smoke.yaml`
- Create: `032-matclaw-cips-curie-temperature/profiles/formal.yaml`
- Create: `033-matclaw-cips-domain-wall-search/profiles/resource.yaml`
- Create: `033-matclaw-cips-domain-wall-search/profiles/platform.yaml`
- Create: `033-matclaw-cips-domain-wall-search/profiles/smoke.yaml`
- Create: `033-matclaw-cips-domain-wall-search/profiles/formal.yaml`
- Modify: `031-matclaw-cips-active-distillation/task.toml`
- Modify: `032-matclaw-cips-curie-temperature/task.toml`
- Modify: `033-matclaw-cips-domain-wall-search/task.toml`
- Modify: `031-matclaw-cips-active-distillation/tests/test.sh`
- Modify: `032-matclaw-cips-curie-temperature/tests/test.sh`
- Modify: `033-matclaw-cips-domain-wall-search/tests/test.sh`
- Create: `tests/hpc/test_matclaw_contract_integration.py`

**Interfaces:**
- All three cases use the same HPC contract and common result envelope.
- Case-specific scientific verifier logic remains in each case.
- Migration order is exactly 032, then 031, then 033.

- [ ] **Step 1: Write failing per-case contract tests**

Assert all three use `hpc_controller`, GPU count 1, paper/formal verifier timeout
at least 7200 seconds, explicit runtime/profile digests, and their existing
case-policy scientific fields.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/hpc/test_matclaw_contract_integration.py -q`

Expected: missing profiles and common result output.

- [ ] **Step 3: Migrate 032 and run its fixture/reference gates**

Run: `.venv/bin/pytest 032-matclaw-cips-curie-temperature/tests tests/hpc/test_matclaw_contract_integration.py -q`

Expected: Curie-temperature convergence and physical-observable checks retain
their current behavior.

- [ ] **Step 4: Migrate 031 and run its lineage/accuracy gates**

Run: `.venv/bin/pytest 031-matclaw-cips-active-distillation/tests tests/hpc/test_matclaw_contract_integration.py -q`

Expected: active-distillation lineage and hidden accuracy behavior are unchanged.

- [ ] **Step 5: Migrate 033 and run adaptive-search gates**

Run: `.venv/bin/pytest 033-matclaw-cips-domain-wall-search/tests tests/hpc/test_matclaw_contract_integration.py -q`

Expected: domain-wall and protocol-provenance behavior are unchanged.

- [ ] **Step 6: Run the combined HPC regression suite**

Run: `.venv/bin/pytest tests/hpc tests/test_matclaw_case_contracts.py tests/test_matclaw_hpc_controller.py tests/test_matclaw_validation.py -q`

Expected: all four HPC cases satisfy the frozen contract and all legacy scientific gates pass.

- [ ] **Step 7: Commit each case separately**

Use three commits so a scientific reviewer can accept or reject each migration independently:

```bash
git commit -m "case(032): migrate Curie workflow to the HPC contract"
git commit -m "case(031): migrate active distillation to the HPC contract"
git commit -m "case(033): migrate domain-wall search to the HPC contract"
```

---

### Task 14: Freeze Ablation-Ready v0 and run excluded pilot pairs

**Files:**
- Create: `releases/ablation-ready-v0.json`
- Create: `experiments/skill-ablation-v1/protocol.yaml`
- Create: `experiments/skill-ablation-v1/README.md`
- Create: `tests/experiments/test_ablation_protocol.py`
- Modify: `summarize.py`

**Interfaces:**
- Release manifest freezes Case, instruction/public, Candidate image, Skill,
  resource/platform, compute runtime, site adapter, verifier, and schema digests.
- Protocol fixes pairing, replicate count, retry policy, pilot exclusion, and
  treatment difference.

- [ ] **Step 1: Write failing comparability tests**

Reject a pair if any field other than Skill availability differs. Reject a
formal record whose release digest is absent or whose experiment ID contains
`pilot`. Exclude `INFRA_INVALID` and retain a separate invalid-run ledger.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/experiments/test_ablation_protocol.py -q`

Expected: release/protocol files and comparability validator are absent.

- [ ] **Step 3: Generate and review the release manifest**

Hash every frozen input and require a clean source commit for release-managed
files. Record the Agent/model exact identity and the immutable Skill bundle
digest. Do not include secrets or mutable image tags without their resolved digest.

- [ ] **Step 4: Define the experiment protocol before observing pilot scores**

Use paired trials on the same site/profile. Fix the replicate count and
predeclare that an infrastructure-invalid attempt may be replaced by a new
attempt with a new run ID, while a scientific or Agent failure may not be
silently rerun.

- [ ] **Step 5: Run one excluded pilot pair per HPC case**

Use experiment ID `skill-ablation-v1-pilot`, one No-Skill and one With-Skill
trial for each of 031–034. Verify pair comparability, lifecycle closure, job
settlement, submission seal, and independent verifier output. Do not use these
eight observations in the formal result.

- [ ] **Step 6: Close pilot findings without changing frozen science**

Infrastructure defects return to the responsible task and require a new v0
release candidate. Any proposed instruction, threshold, or scientific verifier
change requires a new case version and invalidates all prior pilot comparison.

- [ ] **Step 7: Run the complete v0 release gate**

```bash
.venv/bin/pytest tests/core tests/contracts tests/hpc tests/experiments tests/test_workspace_isolation.py tests/test_summarize.py tests/test_matclaw_case_contracts.py tests/test_matclaw_hpc_controller.py tests/test_matclaw_validation.py -q
git diff --check
```

Expected: all tests pass, release manifest validates, and every pilot pair is
comparable or explicitly classified `INFRA_INVALID`.

- [ ] **Step 8: Commit the frozen protocol**

```bash
git add releases/ablation-ready-v0.json experiments/skill-ablation-v1 tests/experiments/test_ablation_protocol.py summarize.py tests/test_summarize.py
git commit -m "experiment: freeze ablation-ready benchmark v0"
```

After this commit, formal paired multi-replicate No-Skill/With-Skill execution
is authorized under the frozen protocol.

---

### Task 15: Migrate the remaining 35 Local cases after the first formal experiment

**Files:**
- Modify: remaining root-level `NNN-slug/task.toml` manifests
- Modify: their `tests/test.sh` verifier wrappers
- Create: `tests/migration/test_all_case_contracts.py`
- Create: `tests/migration/test_all_candidate_bundles.py`
- Create: `docs/migration/local-case-inventory.md`

**Interfaces:**
- Every case resolves through `CaseSpec` and produces a deterministic public-only bundle.
- Every verifier emits a common result object.
- No Local Candidate image contains `bench-hpc` or HPC credentials/configuration.

- [ ] **Step 1: Generate a reviewed inventory, not automatic edits**

For each case record legacy layout, Candidate image, instruction, exact public
files, submission root, verifier inputs, resource limits, and expected fixtures.
Fail the inventory generator if a Docker COPY cannot be represented by an
explicit allowlist.

- [ ] **Step 2: Add all-case contract tests and verify RED**

Run: `.venv/bin/pytest tests/migration/test_all_case_contracts.py tests/migration/test_all_candidate_bundles.py -q`

Expected: unmigrated cases fail with their exact missing contract fields.

- [ ] **Step 3: Migrate in four reviewable batches**

Use batches `002–008`, `010–019`, `020–030`, and `035–041`. After each batch,
run its existing case tests plus both migration suites. Do not change the
scientific expected values while changing manifests/wrappers.

- [ ] **Step 4: Run the full Local smoke matrix**

Run every Local case with the smoke profile and No-Skill treatment. Classify
missing local images or Docker failures as infrastructure invalid, not zero
scientific reward.

- [ ] **Step 5: Commit one batch at a time**

Use commit messages `cases: migrate local contract batch <range>` so failures
can be bisected without reverting unrelated cases.

---

### Task 16: Prove Portable Infrastructure v1 on a second HPC site

**Files:**
- Create: `releases/portable-infrastructure-v1.json`
- Create: `docs/portability/site-conformance.md`
- Create: `tests/portability/test_release_gates.py`
- Modify: `site-configs/example-slurm.yaml`

**Interfaces:**
- Site B supplies only a conformant Adapter/Site Config and compatible platform.
- Cases, prompts, public inputs, scientific verifier logic, thresholds, resource
  profile, and compute runtime digest remain unchanged.

- [ ] **Step 1: Run Site B adapter and environment conformance**

Require all mandatory job operations, idempotency, isolation, settlement,
runtime smoke, platform capture, and quota accounting. A failed check blocks
scientific execution.

- [ ] **Step 2: Reproduce 034, then 032, 031, and 033**

Use new run IDs and site identity. Compare scientific outcomes within declared
tolerances while retaining actual hardware/platform facts. Do not claim strict
score equivalence when the Platform Profile differs.

- [ ] **Step 3: Verify the portability invariant**

Run: `.venv/bin/pytest tests/portability/test_release_gates.py -q`

Expected: the test proves that only Adapter/Site Config and recorded platform
facts differ between Site A and Site B release records.

- [ ] **Step 4: Freeze Portable Infrastructure v1**

Create the release manifest only after 37 Local contracts, four HPC contracts,
both site conformance reports, all independent verifiers, and all run-record
schemas pass.

- [ ] **Step 5: Commit**

```bash
git add releases/portable-infrastructure-v1.json docs/portability/site-conformance.md tests/portability/test_release_gates.py site-configs/example-slurm.yaml
git commit -m "release: freeze portable benchmark infrastructure v1"
```

---

## Final Verification Matrix

Before declaring **Ablation-Ready Infrastructure v0**:

```bash
.venv/bin/pytest tests/core tests/contracts tests/hpc tests/experiments -q
.venv/bin/pytest tests/test_workspace_isolation.py tests/test_summarize.py -q
.venv/bin/pytest tests/test_matclaw_case_contracts.py tests/test_matclaw_hpc_controller.py tests/test_matclaw_runtime_lock.py tests/test_matclaw_validation.py -q
git diff --check
```

Required evidence:

- deterministic bundle manifests for 001, 009, and 031–034;
- no hidden asset in any Candidate bundle/workspace;
- Candidate destroyed before Verifier start;
- hostile submission fixtures rejected by quarantine;
- one immutable run record per attempt;
- no raw HPC credential or SSH socket in Candidate;
- process-test and Slurm conformance reports;
- one excluded No-Skill/With-Skill pilot pair per HPC case;
- all pair-difference checks pass;
- every non-scientific failure has an explicit infrastructure or Agent code.

Before declaring **Portable Infrastructure v1**, additionally require all 37
Local migrations and Site B reproduction without changes to case science or
Verifier logic.
