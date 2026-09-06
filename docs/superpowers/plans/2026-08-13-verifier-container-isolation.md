# Verifier Container Isolation Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every candidate in a clean Docker container without ever exposing hidden tests or reward storage to the candidate container or workspace.

**Architecture:** The candidate container writes an untrusted host workspace and is destroyed when its turn ends. The harness sanitizes that workspace into a private temporary submission, then runs the task's hidden tests in a fresh network-disabled verifier container built from the same pristine compute image; tests, submission, and reward use separate mounts.

**Tech Stack:** Python 3.13, pathlib/shutil/tempfile, Docker CLI, pytest, pagentv4.

## Global Constraints

- Do not change scientific references, task instructions, evaluator thresholds, or solution implementations.
- Preserve all unrelated user changes already present in the dirty worktree.
- Hidden tests must never be copied into `jobs/*/workspace`.
- Candidate `.venv`, `.skills`, test material, and verifier logs must not cross into the verifier submission.
- The candidate container must be stopped before the verifier container starts.
- The verifier must use the task's existing compute image with networking disabled.
- `/submission` and `/tests` are read-only; `/logs/verifier` is a fresh host-owned writable mount; `/tmp` is a private tmpfs.
- Unsafe filesystem nodes fail closed.
- Existing job cleanup removes only exact `workspace/_dftworld_tests` directories.

---

### Task 1: Sanitize untrusted candidate submissions

**Files:**
- Create: `tests/test_verifier_isolation.py`
- Modify: `eval.py`

**Interfaces:**
- Produces: `stage_candidate_submission(workspace: Path, destination: Path) -> None`
- Protected top-level names: `.venv`, `.skills`, `_dftworld_tests`, `tests`, `reference`, `solution`, `.pytest_cache`, `logs`.

- [ ] **Step 1: Write failing sanitizer tests**

Add tests that create ordinary nested outputs, protected directories, a symlink,
and a FIFO. Require ordinary files and symlinks to be copied without following
links, protected names to be absent, and FIFO staging to raise `ValueError`.

```python
def test_stage_candidate_submission_excludes_protected_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    destination = tmp_path / "submission"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "nested" / "result.json").write_text("{}")
    for name in E.PROTECTED_SUBMISSION_NAMES:
        (workspace / name).mkdir()
        (workspace / name / "secret").write_text("hidden")
    E.stage_candidate_submission(workspace, destination)
    assert (destination / "nested" / "result.json").read_text() == "{}"
    assert not any((destination / name).exists() for name in E.PROTECTED_SUBMISSION_NAMES)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: collection or assertion failure because the staging API does not yet exist.

- [ ] **Step 3: Implement minimal safe staging**

Use `os.scandir`/`Path.lstat` and `shutil.copy2`/`copytree(symlinks=True)`.
Reject every node that is not a regular file, directory, or symlink; never
dereference a symlink. Exclude protected names only at the workspace root so
legitimate nested task data remains compatible.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: sanitizer tests pass.

### Task 2: Run hidden tests in an isolated verifier container

**Files:**
- Modify: `tests/test_verifier_isolation.py`
- Modify: `eval.py`

**Interfaces:**
- Produces: `build_verifier_command(image: str, submission: Path, tests_dir: Path, logs_dir: Path, env: dict[str, str] | None) -> list[str]`
- Produces: `verify(workspace: Path, image: str, task_dir: Path, timeout: float, env: dict[str, str] | None = None) -> float`

- [ ] **Step 1: Write failing command-boundary tests**

Assert the command begins with `docker run --rm`, contains `--network none`,
`--workdir /tests`, a private `/tmp` tmpfs, read-only mounts for `/submission`
and `/tests`, and a separate `/logs/verifier` mount. Assert it contains no
`docker exec`, candidate container ID, or candidate workspace mount at `/app`.

```python
def test_verifier_command_has_separate_read_only_boundaries(tmp_path):
    argv = E.build_verifier_command(
        "image", tmp_path / "submission", tmp_path / "tests", tmp_path / "logs",
        {"PROFILE": "paper"},
    )
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network none" in joined
    assert "dst=/submission,readonly" in joined
    assert "dst=/tests,readonly" in joined
    assert "dst=/logs/verifier" in joined
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: failure because `build_verifier_command` does not exist and `verify`
still uses `docker exec` in the candidate container.

- [ ] **Step 3: Implement the verifier command and reward parser**

Construct Docker arguments as a list, validate verifier environment names with
`[A-Za-z_][A-Za-z0-9_]*`, and run this fixed container script:

```bash
shopt -s nullglob dotglob
for item in /submission/*; do cp -a -- "$item" /app/; done
cd /tests
bash /tests/test.sh
```

Use two `TemporaryDirectory` contexts under the candidate workspace parent for
the sanitized submission and reward directory. Read `reward.txt` on the host;
missing or malformed reward returns `0.0`. Always clean both directories.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: isolated verifier command, reward, and cleanup tests pass.

### Task 3: Destroy the candidate before verification

**Files:**
- Modify: `tests/test_verifier_isolation.py`
- Modify: `eval.py`

**Interfaces:**
- `eval_one` calls `await runner.sandbox.close()` after the agent turn and before `verify(...)`.
- `verify` receives `Path(workspace)`, `task.image`, and `task.path`; it no longer accepts a sandbox or candidate container ID.

- [ ] **Step 1: Write a failing lifecycle-order regression**

Use a fake async sandbox plus monkeypatched verifier to record events and assert:

```python
assert events.index("candidate_closed") < events.index("verifier_started")
```

Also assert verification is skipped when candidate shutdown raises.

- [ ] **Step 2: Run the lifecycle test and verify RED**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: verifier currently starts before `runner.close()`/sandbox shutdown.

- [ ] **Step 3: Change `eval_one` lifecycle minimally**

After `consume()` succeeds, close the sandbox, then invoke:

```python
reward = verify(
    Path(workspace), task.image, task.path,
    timeout=task.agent_timeout_sec,
    env=task.verifier_env or None,
)
```

Keep conversation-store cleanup in the existing `finally`. Make repeated
sandbox close safe through the backend's existing close semantics or an
explicit local `candidate_closed` guard.

- [ ] **Step 4: Run focused and architecture tests**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py tests/test_workspace_isolation.py -q`

Expected: all tests pass and no test writes hidden assets into a workspace.

### Task 4: Document and verify the new boundary

**Files:**
- Modify: `README.md`
- Modify: `tests/test_verifier_isolation.py`

**Interfaces:**
- Documentation states that candidate and verifier are separate containers and lists retained versus removed artifacts.

- [ ] **Step 1: Add a static regression for forbidden staging**

Assert the source of `verify` does not contain `_dftworld_tests`, `docker_exec`,
or `/app/_dftworld_tests`, and does contain `docker`, `run`, `/submission`, and
`/tests`.

- [ ] **Step 2: Run it and verify current behavior is covered**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py -q`

Expected: PASS only after Tasks 1–3.

- [ ] **Step 3: Update README security-boundary documentation**

Replace the claim that hidden assets simply never enter agent view with the
stronger lifecycle: public-only candidate container, candidate destruction,
sanitized handoff, clean verifier container, read-only hidden tests, and
host-owned reward.

- [ ] **Step 4: Run source checks**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py tests/test_workspace_isolation.py -q`

Run: `git diff --check -- eval.py tests/test_verifier_isolation.py README.md`

Expected: all tests pass and no whitespace errors.

### Task 5: End-to-end verification and precise legacy cleanup

**Files:**
- Modify only runtime evidence under: `jobs/*/threads/*/workspace/_dftworld_tests/` (delete exact directories)

**Interfaces:**
- No source API changes.

- [ ] **Step 1: Run the full local architecture suite**

Run: `.venv/bin/pytest tests/test_verifier_isolation.py tests/test_workspace_isolation.py tests/test_chemgraph_case_contracts.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run lightweight end-to-end evaluation when its image exists**

Run: `.venv/bin/python eval.py 001-hello --no-skills --experiment verifier-isolation-smoke`

Expected: `PASS`, reward `1.0`, and no `_dftworld_tests` in its workspace.

- [ ] **Step 3: Run one scientific end-to-end evaluator when its image exists**

Use the existing validated oracle workspace or a normal 025 evaluation; verify
that the separate verifier container recomputes the scientific result and
returns reward `1.0` without introducing hidden files into the candidate
workspace.

- [ ] **Step 4: Enumerate and remove only legacy hidden-test copies**

Enumerate exact matches with:

```bash
find jobs -type d -path '*/threads/*/workspace/_dftworld_tests' -print
```

After confirming the count, delete exactly those printed directories. Do not
delete job workspaces, summaries, messages, CTRF reports, outputs, or rewards.

- [ ] **Step 5: Verify cleanup and final source state**

Run:

```bash
test "$(find jobs -type d -path '*/threads/*/workspace/_dftworld_tests' | wc -l | tr -d ' ')" = 0
.venv/bin/pytest tests/test_verifier_isolation.py tests/test_workspace_isolation.py tests/test_chemgraph_case_contracts.py -q
git diff --check -- eval.py tests/test_verifier_isolation.py README.md
```

Expected: zero retained hidden-test directories, all selected tests pass, and no
diff-check errors.
