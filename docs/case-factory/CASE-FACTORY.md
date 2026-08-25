# Case Factory

> Scope: P0 Target Adapter + Runnable Local Draft.  Converts an approved Builder
> `case-design.yaml` and generic scaffold into a dftworld-executable Draft Case
> (`task.toml` v1.2 + root `Dockerfile`).  Generated Draft stays
> `benchmark_valid=false` until the case-internal G release gates pass.

## Baseline (recorded 2026-08-19, commit `53a94f8`)

Before any adapter code, frozen baseline:

```text
CF0  Builder  PASS       91      (.venv/bin/python -m unittest -v
                                scientific-benchmark-case-builder-portable/tests/test_portable_package.py
                                .../test_common_builder.py .../test_mlp_category.py)
CF0B Forward smoke         PASS_REPORTED
CF0C Forward reproducibility PENDING
CF1  Infra     PASS       200, 1 skipped   (.venv/bin/pytest tests/contracts tests/core tests/hpc -q)
```

## P0 status (recorded 2026-08-19)

```text
CF2  Target Adapter  PASS  dftworld_bench/case_factory adapter renders
                            task.toml v1.2 + Dockerfile + .dockerignore +
                            source/dftworld-target.lock.json byte-stably;
                            render/validate/diff agree; fixtures
                            mlp-local-final-retraining (benchmark/042) and
                            mlp-hpc-end-to-end (benchmark/043) pass CaseSpec.load,
                            eval.load_task, Packager, Candidate audit
CF3  Runnable Draft  PASS     harness + verifier + record + real container
                            Candidate + fresh-Verifier exec all green on
                            dftworld-base-mace (authorized live smoke, evidence
                            on disk, full factory gates promoted,
                            benchmark_valid stays false)
```

P0.1 closure (same day):

- default `render` refuses drifted generated files; explicit
  `--force-generated` shows the diff and overwrites
- generated-set commit is transactional: every existing file backed up first,
  any failure rolls the whole set back and removes files this commit created
  (fresh-render rollback), lock committed last.  This is exception atomicity,
  not crash atomicity — SIGKILL/power loss needs a future startup recovery
- production `case_factory smoke CASE [--candidate docker|audit]
  [--runner docker|audit]` drives the real Trusted Harness through
  `run_verifier()`/`build_verifier_command`; on `FailureCode.PASS` it derives
  the contract gates `runtime_contract_valid` + `verifier_command_valid`.
  `--candidate docker` builds the generated Case image and runs it as an
  isolated container (network none, non-root, read-only root, no host
  HOME/credentials); the full gates `runtime_valid`/`candidate_smoke_valid`
  promote ONLY when `--candidate docker` AND `--runner docker` both ran real
  containers.  Tests never write gates.
- local fixture ships a real `tests/test.sh` Verifier entry (writes a
  schema-conforming `/logs/verifier/result.json`, PASS on the declared bytes)
  so `--runner docker` can execute the real container path.
- `smoke --runs-dir DIR` persists the RunRecord, sealed submission, candidate
  and verifier logs as durable CF3 evidence and returns their paths + sha256
  digests.
- CF3 closed by an authorized real-container smoke on dftworld-base-mace:
  `--candidate docker --runner docker` built dftworld-smoke-<task>, ran the
  Candidate isolated (network none, non-root 65532:65532, read-only, fresh
  HOME, single /workspace mount) and executed the fixture tests/test.sh in a
  fresh Verifier container (PASS/VALID_RESULT, exit 0).  All four runtime
  gates promoted; benchmark_valid stays false; check_release still blocks.
  Evidence retained at `evidence/case-factory-smoke/042-local-final-retraining/`
  with run_record_sha256 / sealed_submission_sha256 digests in the dashboard.

Fresh suite counts after the real-container run: `732 passed, 1 skipped`
(`.venv/bin/pytest tests/ -q`, includes the uncommitted HPC-gateway tests),
`200 passed, 1 skipped` (contracts/core/hpc), `89 passed` (tests/case_factory),
`95 passed` (portable Builder unittest), `bash install.sh --check` and
`bash -n install.sh` both clean.

## CLI

```bash
.venv/bin/python -m dftworld_bench.case_factory render CASE --target dftworld [--force-generated]
.venv/bin/python -m dftworld_bench.case_factory validate CASE --target dftworld
.venv/bin/python -m dftworld_bench.case_factory diff CASE --target dftworld
.venv/bin/python -m dftworld_bench.case_factory smoke CASE --target dftworld [--candidate docker|audit] [--runner docker|audit] [--runs-dir DIR]
```

JSON stdout, nonzero on failure.  Render into a private temporary directory,
validate, then replace the generated set transactionally (backup-all, commit,
rollback-whole on failure).  One explicit Case directory per invocation; never
scan HOME or shared scratch.

Default `render` refuses to overwrite generated files that drifted from the
adapter's expectation; `--force-generated` overrides after showing the diff.

`validate` recomputes the expected lock from the gates recorded on disk, so it
accepts both render-false and committed-true locks.  `diff` reports any byte
drift between disk and the adapter's expectation; a clean fixture reports
`unchanged`.

`smoke` runs the Draft through the real Trusted Harness with a pluggable
Candidate and real `run_verifier`.  `--candidate audit` (default) is an
isolated subprocess; `--candidate docker` builds the generated Case Dockerfile
and runs it as an isolated container (network none, non-root 65532:65532,
read-only root, private /tmp, fresh HOME, no host credentials).  `--runner
audit` asserts the verifier's isolation argv and simulates container output for
offline runs; `--runner docker` executes the real container.  Only a
`FailureCode.PASS` derives gates and reports `valid=true` with exit 0 — a
`SCIENTIFIC_FAIL` is counted but exits nonzero with gates unset.  Contract gates
`runtime_contract_valid`/`verifier_command_valid` come from any runner; the full
`runtime_valid`/`candidate_smoke_valid` promote only when `--candidate docker`
AND `--runner docker` both ran real containers.  `--runs-dir DIR` persists
RunRecord, sealed submission and verifier logs and reports their paths + sha256
digests.  `benchmark_valid` is never touched.

## Gate vocabulary — four distinct writers

| Writer            | Meaning                                                          | Example values |
|-------------------|------------------------------------------------------------------|----------------|
| `case_status`     | lifecycle state of the case dir (existing)                       | `constructed`, `benchmark_valid` |
| `factory_gates`   | derived, orthogonal gates set only by independent checks         | `design_valid`, `target_adapter_valid`, `runtime_contract_valid`, `verifier_command_valid`, `runtime_valid`, `candidate_smoke_valid`, `discovery_complete`, `diagnosis_complete` |
| CF dashboard      | acceptance-board gates for Case Factory work (Task-level)        | `CF0`..`CF12` |
| G release gates   | case-internal release gates, never reused by CF dashboard        | `G0`..`G12` |

`factory_gates` is recorded inside the target lock (Task 2/5).  Rendering may
set only `design_valid` and `target_adapter_valid`; `runtime_valid` and
`candidate_smoke_valid` stay false until their independent checks pass.

```yaml
factory_gates:
  design_valid: true
  target_adapter_valid: false
  runtime_contract_valid: false
  verifier_command_valid: false
  runtime_valid: false
  candidate_smoke_valid: false
  discovery_complete: false
  diagnosis_complete: false
```

`hidden_tokens` is an audit inventory size, not a leakage count.  Acceptance
uses `audit.valid=true` and `errors=[]`.

## Architecture

```text
case-design.yaml (scientific authoring)
  -> dftworld_bench/case_factory (repository-owned Target Adapter)
  -> task.toml v1.2 + Dockerfile + .dockerignore + source/dftworld-target.lock.json
  -> CaseSpec.load / eval.load_task / Packager / Candidate audit
  -> Local Candidate smoke (scripted Candidate, no external model)
  -> benchmark_valid=false, check_release still blocks
```

Target Adapter is dftworld Infrastructure, not a Skill.  Execution class is
approved in Case Design; the adapter validates and renders it, never guesses or
changes it.  Mounts, networks, secrets, site config, scheduler credentials and
Gateway wiring are infrastructure-owned.

Portable Builder generic mode stays portable.  Inside dftworld,
`init_case.py --target dftworld` invokes the repository CLI and does not
duplicate adapter logic.
