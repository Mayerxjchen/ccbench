# Verifier Container Isolation Design

Date: 2026-08-13

## Goal

Prevent a candidate agent from observing or modifying hidden tests, verifier
state, or the final reward. Preserve the existing task contract: the agent sees
the instruction, the compute image, and the files explicitly copied into
`/app`; the hidden evaluator continues to read candidate artifacts at `/app`.

This change addresses four concrete defects in the current harness:

1. candidate and verifier execute sequentially in the same container;
2. hidden tests are staged through candidate-controlled
   `/app/_dftworld_tests`;
3. candidate background processes can survive into verification and can reach
   `/logs/verifier/reward.txt`;
4. completed job workspaces retain copies of hidden tests.

It does not change scientific references, evaluator thresholds, or task
instructions.

## Considered Approaches

### A. Kill candidate processes, then verify in the same container

This is the smallest change, but process cleanup is difficult to prove complete
and the candidate has already controlled the container filesystem. A modified
interpreter, package, shell startup file, or verifier path could survive process
termination. Rejected.

### B. Start a clean verifier container and mount the candidate workspace

This removes surviving processes, but mounting the complete candidate workspace
at `/app` also supplies candidate-controlled `.venv`, Python packages, and
configuration to the verifier. Existing evaluators call tools such as
`/app/.venv/bin/python`, so the candidate could replace the verifier runtime.
Rejected as insufficient by itself.

### C. Clean verifier container plus sanitized submission staging

Use the same pinned compute image to start a new, network-disabled verifier
container. Before starting it, copy candidate-created task artifacts into a new
host-owned staging directory while excluding protected harness/runtime names.
Mount that staging directory read-only at `/submission`, mount hidden tests
read-only at `/tests`, and mount a fresh host-owned reward directory at
`/logs/verifier`. Inside the clean verifier container, copy the sanitized
submission into the image's pristine `/app`, then execute `/tests/test.sh` from
`/tests`.

This is the selected approach. It preserves the pristine image-provided
`/app/.venv` while keeping candidate processes and hidden assets separated.

## Security Boundary

### Candidate phase

- One candidate container receives the instruction and public task inputs.
- It may write its `/app` workspace.
- It has no mount containing `reference/`, `solution/`, `tests/`, or verifier
  logs.
- When the agent turn ends, the candidate container is stopped before hidden
  tests are staged anywhere.

### Handoff phase

- The workspace remains on the host only as untrusted candidate data.
- A sanitizer copies it into a fresh temporary submission directory.
- The sanitizer rejects sockets, devices, FIFOs, and path escapes.
- The sanitizer excludes candidate-controlled runtime/harness paths:
  `.venv`, `.skills`, `_dftworld_tests`, `tests`, `reference`, `solution`,
  `.pytest_cache`, and verifier-log paths.
- Symlinks are not followed. A symlink in a required deliverable remains an
  invalid artifact rather than becoming a path traversal.
- The original job workspace is never modified or populated with hidden tests.

This is a denylist compatibility layer for legacy tasks. A future schema should
replace it with an explicit per-task artifact allowlist. Until that migration,
tests must treat all submission files as untrusted.

### Verifier phase

- A new container is created from the task's pinned compute image.
- Network is disabled.
- `/submission` is a read-only mount of sanitized candidate data.
- `/tests` is a read-only mount of hidden evaluator files.
- `/logs/verifier` is a new writable host-owned temporary directory.
- `/tmp` is a private tmpfs for pytest, vibrations, and test fixtures.
- The verifier starts with the image's pristine `/app`, including its trusted
  `.venv`; sanitized submission files are copied into `/app` without replacing
  protected runtime paths.
- The working directory is `/tests`, preventing candidate `pytest.ini`,
  `conftest.py`, or `sitecustomize.py` in `/app` from becoming verifier startup
  configuration.
- Verifier environment variables come only from `task.toml [verifier.env]`.
- Reward is read by the host from the temporary verifier-log directory after
  the container exits.
- The temporary submission and reward directories are removed in `finally`.

## Data Flow

```text
instruction + public + pristine image
                  |
                  v
         candidate container A
                  |
          untrusted workspace
                  |
        stop/destroy container A
                  |
      sanitize into private staging
                  |
                  v
   clean verifier container B -- read-only /tests
                  |
                  v
       host-owned reward directory
```

No hidden test file is ever placed in the candidate workspace.

## Compatibility

- Existing evaluator paths `/app`, `/tests`, `/tmp`, and
  `/logs/verifier/reward.txt` remain valid.
- The verifier uses the same task image, so MACE, TBLite, CP2K, DeePMD, and
  MatClaw dependencies remain available.
- Tests that create temporary files continue to use the verifier's private
  `/tmp`.
- Candidate modifications to legitimate task inputs, such as a completed CP2K
  input or generated `input.json`, are copied as submission data.
- Candidate replacement of image-owned `.venv` or injected `.skills` is
  discarded during handoff.

## Failure Handling

- Failure to stop the candidate container aborts verification and returns no
  reward.
- Unsafe candidate filesystem entries cause fail-closed reward `0`.
- Missing `reward.txt`, malformed reward content, verifier timeout, or Docker
  failure returns reward `0` and records the verifier diagnostic.
- Temporary directories are removed on success, failure, and timeout.
- The verifier command must never fall back to the candidate container.

## Tests

Implementation follows test-driven development. Required regressions:

1. verifier construction never references the candidate container ID;
2. hidden tests are mounted directly from the task's `tests/` directory and are
   never copied under the candidate workspace;
3. candidate `.venv`, `.skills`, `_dftworld_tests`, and verifier logs are absent
   from sanitized submission staging;
4. ordinary files and nested task artifacts survive staging;
5. unsafe filesystem entries fail closed;
6. verifier Docker arguments include a new container, disabled network,
   read-only hidden/submission mounts, private tmpfs, `/tests` workdir, and a
   separate writable reward mount;
7. verifier environment variables are passed only to the verifier container;
8. temporary submission/reward directories are cleaned after all exit paths;
9. existing workspace-seeding isolation tests remain green;
10. at least one light oracle task and one 025–030 scientific task pass through
    the new end-to-end verifier path when their images are available.

## Existing Job Cleanup

The 46 retained `jobs/*/threads/*/workspace/_dftworld_tests` directories are
hidden-test disclosures. Cleanup is a separate, explicitly scoped destructive
operation:

- enumerate exact `_dftworld_tests` directories under `jobs/`;
- remove only those directories after implementation verification;
- retain candidate outputs, messages, summaries, CTRF reports, and rewards;
- verify that no `_dftworld_tests` path remains.

The harness change prevents new copies from being created.

## Completion Criteria

- Candidate and verifier never share a running container.
- Candidate-controlled processes and runtimes cannot participate in scoring.
- Hidden tests and reward storage are outside the candidate workspace.
- Unit regressions and selected end-to-end verifier runs pass.
- Existing hidden-test workspace copies are removed only after the user-approved
  cleanup step.
