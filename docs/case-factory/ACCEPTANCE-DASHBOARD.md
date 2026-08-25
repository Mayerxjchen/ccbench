# Case Factory Acceptance Dashboard (CF0..CF12)

> Orthogonal to case-internal G0..G12 release gates.  A box checks only when it
> is independently executed, verified and frozen — never on "code written" or
> "should run".  Live counts are updated by the task that closes each gate.

## Baseline (recorded 2026-08-19, commit `53a94f8`)

```text
CF0   Builder baseline          PASS        91 tests      (.venv/bin/python -m unittest -v portable tests)
CF0B  Forward smoke             PASS_REPORTED
CF0C  Forward reproducibility   PENDING
CF1   Infra baseline            PASS        200 + 1 skip (.venv/bin/pytest tests/contracts tests/core tests/hpc -q)
CF2   Target Adapter            PASS        dftworld Case Factory: 89 case_factory tests + 95 portable tests
CF3   Runnable Draft            PASS        harness + verifier + record + real container Candidate + fresh-Verifier exec
CF4   Discovery                 PENDING
CF5   Diagnosis                 PENDING
CF6   Real Local Case           PENDING
CF8   HPC Runtime               PENDING
```

## P0 status (recorded 2026-08-19)

```text
CF2   Target Adapter            PASS   adapter render/validate/diff byte-stable; fixtures render clean;
                                        CaseSpec.load + eval.load_task + Packager + audit agree on
                                        mlp-local-final-retraining and mlp-hpc-end-to-end

CF3   Runnable Draft            PASS
        PASS:
          - task.toml / Dockerfile / .dockerignore / lock render clean
          - CaseSpec.load + eval.load_task + Packager + Candidate audit agree
          - full Trusted Harness lifecycle (package -> candidate_start ->
            agent_start -> agent_stop -> candidate_freeze ->
            submission_collect -> candidate_destroy -> quarantine ->
            verifier_start -> record_write) in fixed order
          - quarantine + immutable Run Record written
          - run tagged case-construction-smoke, excluded from formal/pilot stats
          - benchmark_valid=false and check_release still blocks
          - isolated Candidate start: real child process, private HOME,
            workspace cwd; real terminate/reap teardown
          - Verifier through real run_verifier()/build_verifier_command();
            isolation argv (non-root, no-network, read-only, cap-drop ALL)
            asserted
          - production `case_factory smoke` derives runtime_contract_valid +
            verifier_command_valid contract gates; tests never write gates
            directly
          - `DockerScriptedCandidate` built: generates the Case image and runs
            it isolated (network none, non-root, read-only, fresh HOME, single
            workspace mount); argv isolation asserted without a docker daemon
          - local fixture ships a real `tests/test.sh` Verifier entry writing a
            schema-conforming result.json (PASS on declared bytes)
          - full runtime gates promote only when --candidate docker AND
            --runner docker both ran real containers (pure gate logic tested)
          - `smoke --runs-dir` persists RunRecord / sealed submission / verifier
            logs as durable evidence with returned paths + sha256 digests
          - render rollback covers fresh commits too: files a failed commit
            created are removed (exception atomicity, documented as such)
          - smoke CLI success requires FailureCode.PASS — a SCIENTIFIC_FAIL is
            counted but exits nonzero with contract gates unset
          - REAL CONTAINER EXEC (authorized live smoke, dftworld-base-mace,
            evidence/ run on a fixture copy):
              docker build dftworld-smoke-042-local-final-retraining + isolated
              container Candidate wrote /workspace/final/result.txt =
              "final accuracy 0.9876"; fresh-Verifier container exec ran the
              fixture tests/test.sh and returned PASS/VALID_RESULT; exit 0
          - full factory gates promoted by the real run: runtime_contract_valid,
            verifier_command_valid, runtime_valid, candidate_smoke_valid all
            true; benchmark_valid stays false and check_release still blocks
          - durable evidence on disk:
              evidence/case-factory-smoke/042-local-final-retraining/
                case/ (fixture copy, gates committed)
                runs/ (RunRecord + sealed-submission + verifier-logs)
                run_record_sha256=6bd5f213598d0da703647134c07ec4c3880a31d33
                                 cf679390570335cb77bcbdf
                sealed_submission_sha256=f839b3be235625322c8606a215bafe78f7a08e
                                      5ab787e56669ba3a862660b50c
        PENDING: (none)
```

Full suites that stay green after the real-container run: `732 passed,
1 skipped` (.venv/bin/pytest tests/ -q, includes the uncommitted HPC-gateway
tests) plus `95 passed` (portable Builder unittest suites), `89 passed`
(case_factory). `install.sh --check` and `bash -n install.sh` both clean.

## Gate map

| ID  | Gate                     | Meaning                                                        | Closes when |
|-----|--------------------------|----------------------------------------------------------------|-------------|
| CF0 | Builder baseline         | portable Builder test suite at baseline                        | recorded (91) |
| CF0B| Forward smoke            | builder forward smoke reported                                 | reported |
| CF0C| Forward reproducibility  | builder forward reproducibility re-derived                      | PENDING |
| CF1 | Infra baseline           | contracts/core/hpc test suites at baseline                     | recorded (200+1) |
| CF2 | Target Adapter           | design, TOML, Dockerfile, drift, CaseSpec, eval, Packager, audit all pass | PASS (2026-08-19) |
| CF3 | Runnable Draft           | generated Local Candidate completes Harness + separate Verifier + Run Record, release still blocked | PASS (2026-08-19) — real container Candidate + fresh-Verifier exec both green |
| CF4 | Discovery                | Discovery Run (later plan)                                     | PENDING |
| CF5 | Diagnosis                | Trusted Diagnosis (later plan)                                 | PENDING |
| CF6 | Real Local Case          | real (non-scripted) local case                                 | PENDING |
| CF8 | HPC Runtime              | qualified HPC runtime/formal profile                           | PENDING |

CF7 / CF9..CF12 reserved for later-phase gates (release re-freeze, multi-case
pilot, ablation pairing): CF7, CF9, CF10, CF11, CF12 all PENDING.

## Rules

- Dashboard gates never reuse G0..G12 naming.
- `benchmark_valid` stays false for every Draft; `check_release` must still
  block after CF2/CF3 close.
- `hidden_tokens` is an audit inventory size; acceptance is
  `audit.valid=true` and `errors=[]`, never `hidden_tokens == 0`.
- API/model and Docker live tests are opt-in and never run in CI.

## HpcDispatcher activation gates (D-series, 2026-08-22)

Derived by `scripts/infra/activate_v2.py --mode simulated --json`. Deterministic
gates D0–D10 PASS from the test suite; real-site gates D11/D12 stay NOT_RUN
until the authorized the site canary receipt and five-lineage Pilot evidence exist.
Formal is never enabled by a simulated run.

| Gate | Title | Status |
|---|---|---|
| D0 | baseline suite green (5 known Task-12-owned expected failures only) | PASS |
| D1 | dispatcher facade parity (7 operations + rejection paths) | PASS |
| D2 | execution request v2 + content-addressed input staging | PASS |
| D3 | operation-attempt lifecycle (fsynced intent / marker adoption / v2 CLI) | PASS |
| D4 | driver conformance across backends + Formal rejects ProcessDriver | PASS |
| D5 | trusted SiteProfile freeze (credential-free digest, abstract capabilities) | PASS |
| D6 | runtime wrapper containment (adversarial rejections + qualification probe) | PASS |
| D7 | production path enters through HpcDispatcher only (source scans) | PASS |
| D8 | bundled skill contract (no site facts / raw remote workflow) | PASS |
| D9 | settlement + evidence semantics (exact job IDs, immutable report) | PASS |
| D10 | deterministic fault-injection matrix (incl. voided-intent lineage rule) | PASS |
| D11 | real-site canary qualification receipt (`evidence/hpc-dispatcher/qualification/site-v1/receipt.json`) | NOT_RUN — needs user authorization |
| D12 | five-lineage Pilot (031–034/042, NS+WS, agent_turn_limit=128) | NOT_RUN — needs user authorization |

Expected-failure ledger for D0: readiness-audit 031/032/033 pilot_eligible,
release-reproducibility digest set, and the v1 protocol skills_sha consistency
check — all owned by the Task-12 release re-freeze.
