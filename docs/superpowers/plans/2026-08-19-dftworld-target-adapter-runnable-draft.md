# dftworld Target Adapter and Runnable Draft Implementation Plan

> For agentic workers: use subagent-driven-development or executing-plans task by task.

Goal: Convert an approved Builder case-design.yaml and generic scaffold into a dftworld-executable Draft Case with task.toml v1.2 and a root Dockerfile. The generated case must pass CaseSpec, eval.load_task, Packager, Candidate audit, and an isolated Local Candidate smoke while remaining benchmark_valid=false.

Architecture: Implement repository-specific rendering in dftworld_bench/case_factory. YAML remains the scientific authoring source; generated TOML is the executable contract. The portable Builder may call the repository CLI with --target dftworld but must not duplicate adapter logic.

Tech stack: Python 3.12+, PyYAML, tomllib, JSON, SHA-256, Dockerfile generation, pytest/unittest, existing CaseSpec, Packager and Trusted Harness.

## Global Constraints

- P0 only: Target Adapter plus Runnable Local Draft.
- Do not implement Discovery diagnosis, shared scientific Verifier modules, formal Gateway wiring, or multi-case pilots here.
- Adapter code belongs in dftworld_bench/case_factory, not the portable Skill.
- task.toml schema 1.2 is the canonical executable manifest for this phase.
- Keep task.yaml parsing but do not emit task.yaml.
- Do not migrate all legacy cases.
- New cases explicitly declare execution.class.
- Execution class is approved in Case Design; the adapter validates and renders it but never guesses or changes it.
- Mounts, networks, secrets, site config, scheduler credentials and Gateway wiring are infrastructure-owned.
- Generated Drafts use submission_root=final and legacy_submission_layout=false.
- Dockerfile shape is a validated FROM plus COPY public/ /app/.
- Hidden assets must not enter Candidate content or Docker build context.
- Local official execution remains sandboxed and uses a fresh Verifier after Candidate destruction.
- Runtime smoke never writes benchmark_valid=true.
- API/model and Docker live tests are opt-in.
- Preserve unrelated untracked project data.

## Gate Vocabulary

Keep existing case_status. Add orthogonal derived factory gates:

~~~yaml
factory_gates:
  design_valid: true
  target_adapter_valid: false
  runtime_valid: false
  candidate_smoke_valid: false
  discovery_complete: false
  diagnosis_complete: false
~~~

Dashboard uses CF0 through CF12. Case release keeps G0 through G12.

## P0 Acceptance

~~~text
case-design
-> Target Adapter
-> task.toml + Dockerfile + target lock
-> CaseSpec.load
-> eval.load_task
-> package_candidate
-> Candidate audit
-> isolated Candidate start
-> separate Verifier
-> Run Record
-> benchmark_valid=false
-> release remains blocked
~~~

hidden_tokens is an audit inventory size, not a leakage count. Acceptance uses audit.valid=true and errors=[].

---

### Task 1: Freeze baseline and create CF dashboard

Files:
- Create docs/case-factory/CASE-FACTORY.md
- Create docs/case-factory/ACCEPTANCE-DASHBOARD.md
- Create tests/case_factory/test_baseline_contract.py

Requirements:
- Dashboard IDs are CF0 through CF12.
- Record command, count, skip count, commit and timestamp.
- Initial values:
  - CF0 Builder PASS, 91
  - CF0B Forward smoke PASS_REPORTED
  - CF0C Forward reproducibility PENDING
  - CF1 Infra PASS, 200 plus 1 skipped
  - CF2 Target Adapter PENDING
  - CF3 Runnable Draft BLOCKED
- Document separate writers and meanings for case_status, factory_gates, CF dashboard gates and G release gates.
- Test that the dashboard never reuses G0 Builder Baseline naming.
- Run .venv/bin/pytest tests/case_factory/test_baseline_contract.py -q.

---

### Task 2: Define repository Target Adapter contract

Files:
- Create dftworld_bench/case_factory/__init__.py
- Create dftworld_bench/case_factory/target.py
- Create dftworld_bench/case_factory/state.py
- Create schemas/dftworld-target.schema.json
- Create tests/case_factory/test_target_contract.py

Interface:

~~~python
class TargetAdapter(Protocol):
    name: str
    version: str

    def validate_design(self, case_dir: Path, design: dict) -> TargetVerdict: ...
    def render(self, case_dir: Path, design: dict) -> tuple[GeneratedFile, ...]: ...
    def validate_output(self, case_dir: Path, design: dict) -> TargetVerdict: ...
~~~

Required Case Design additions:

~~~yaml
identity:
  case_id: 042-example
  task_name: benchmark/042-example
  title: Example
  description: Example case
  case_version: 1.0.0

execution:
  class: local_sandbox

runtime:
  candidate_image: dftworld-base-mace
  build_timeout_sec: 1800
  agent_timeout_sec: 7200
  verifier_timeout_sec: 3600
  cpus: 4
  memory_mb: 8192
  storage_mb: 20480
  gpus: 0
  allow_internet: false

submission:
  root: final

candidate_files:
  - source: public/**
    destination: .
    strip_prefix: public
~~~

Tests must reject missing identity, invalid execution, missing image/resources/timeouts, negative resources, site credentials, free-form Docker instructions, and incomplete HPC capability/profile/runtime declarations.

Every render writes source/dftworld-target.lock.json containing target, adapter version, Case Design SHA-256 and generated-file hashes. Timestamps do not participate in reproducibility digests.

---

### Task 3: Render canonical task.toml v1.2

Files:
- Create dftworld_bench/case_factory/dftworld_target.py
- Create tests/case_factory/test_dftworld_task_manifest.py
- Modify docs/architecture/CASE-STANDARD.md

Local output must contain schema_version 1.2, case_version, execution, candidate, task, verifier, agent, environment and generated tables.

HPC output additionally contains hpc.contract_version and required_capabilities.

Requirements:
- Fixed small TOML renderer; reject control characters and unsafe paths.
- Runtime-ignored generated table records target, adapter version and Case Design digest.
- Prove CaseSpec.load and eval.load_task agree on execution, submission root, timeouts and resources.
- Existing legacy contract/workspace tests remain unchanged and green.

---

### Task 4: Generate safe Dockerfile and dockerignore

Files:
- Extend dftworld_bench/case_factory/dftworld_target.py
- Create tests/case_factory/test_dftworld_dockerfile.py

Generated Dockerfile:

~~~dockerfile
FROM dftworld-base-mace
COPY public/ /app/
~~~

Requirements:
- Reject image references containing whitespace, directives, shell metacharacters, paths, credentials or build arguments.
- Generate .dockerignore excluding reference, solution, tests, profiles, evidence, source, Git, jobs, keys and credentials.
- Parse permitted instructions deterministically without Docker.
- Optional build test runs only when DFTWORLD_RUN_DOCKER_TESTS=1 and the base image already exists.

---

### Task 5: Detect generated-file drift and derive factory state

Files:
- Extend dftworld_bench/case_factory/dftworld_target.py
- Extend dftworld_bench/case_factory/state.py
- Create tests/case_factory/test_target_drift.py

Requirements:
- RED-test changes to execution, visibility, image, resources, timeouts, Dockerfile and lock digests.
- validate_output recomputes expected bytes without writing.
- Rendering may set only design_valid and target_adapter_valid.
- Runtime and smoke gates remain false until their independent checks pass.
- Default regeneration refuses drift.
- Explicit regeneration shows a diff and replaces only task.toml, Dockerfile, .dockerignore and target lock.
- Never overwrite instruction, public, source science, reference, solution, tests, profiles or evidence.

---

### Task 6: Add dftworld Case Factory CLI

Files:
- Create dftworld_bench/case_factory/__main__.py
- Create dftworld_bench/case_factory/cli.py
- Create tests/case_factory/test_case_factory_cli.py

Commands:

~~~bash
.venv/bin/python -m dftworld_bench.case_factory render CASE --target dftworld
.venv/bin/python -m dftworld_bench.case_factory validate CASE --target dftworld
.venv/bin/python -m dftworld_bench.case_factory diff CASE --target dftworld
~~~

Requirements:
- JSON stdout and nonzero failures.
- No partial writes on invalid design.
- Render into a private temporary directory, validate, then atomically replace only generated files.
- Accept one explicit Case directory; never scan HOME or shared scratch.
- Add CLI help to CASE-FACTORY.md.

---

### Task 7: Bridge portable Builder without duplicating logic

Files:
- Modify portable scripts/common/init_case.py
- Modify portable SKILL.md and README.md
- Modify package tests and SHA256SUMS

Requirements:
- Add optional --target dftworld.
- Generic mode remains byte-compatible.
- Outside dftworld, target mode fails clearly and preserves generic scaffold.
- Inside dftworld, invoke python -m dftworld_bench.case_factory render.
- Adapter failure leaves no partial runtime files.
- Portable package still installs and validates without dftworld.

---

### Task 8: Add deterministic Local and HPC runtime fixtures

Files:
- Create tests/case_factory/fixtures/mlp-local-final-retraining/
- Create tests/case_factory/fixtures/mlp-hpc-end-to-end/
- Create tests/case_factory/test_dftworld_runtime_fixtures.py

Local acceptance:
- Target render PASS
- CaseSpec.load PASS
- eval.load_task PASS
- package_candidate PASS
- deterministic bundle manifest
- Candidate audit valid=true and errors=[]
- Dockerfile parse PASS
- benchmark_valid=false
- check_release blocked

HPC acceptance:
- explicit hpc_controller
- hpc capability contract
- resource/platform/compute-runtime files present
- CaseSpec/eval/Packager PASS
- no hostname/partition/account/SSH
- benchmark_valid=false

Fixtures stay compact and never submit HPC. Never assert hidden_tokens equals zero.

---

### Task 9: Add deterministic Local Candidate-start smoke

Files:
- Create tests/case_factory/test_runnable_local_draft.py
- Create tests/case_factory/scripted_candidate.py
- Modify dftworld_bench/case_factory/state.py

Requirements:
- Test-only scripted Candidate uses Trusted Harness without external model/API.
- Require lifecycle:
  package, candidate_start, agent_start, agent_stop, candidate_freeze,
  submission_collect, candidate_destroy, quarantine, verifier_start, record_write.
- Candidate writes declared final output; separate Verifier validates it.
- Tag run as case-construction-smoke and exclude it from formal statistics.
- Set runtime_valid=true and candidate_smoke_valid=true.
- Keep benchmark_valid=false and require check_release failure.
- Document optional live PAgent smoke, never run it in CI.

---

### Task 10: Preserve regressions and close CF2/CF3

Files:
- Update docs/case-factory/ACCEPTANCE-DASHBOARD.md
- Update docs/case-factory/CASE-FACTORY.md
- Extend relevant package/core tests

Fresh commands:

~~~bash
.venv/bin/python -m unittest -v \
  scientific-benchmark-case-builder-portable/tests/test_portable_package.py \
  scientific-benchmark-case-builder-portable/tests/test_common_builder.py \
  scientific-benchmark-case-builder-portable/tests/test_mlp_category.py

.venv/bin/pytest tests/contracts tests/core tests/hpc tests/case_factory -q
bash scientific-benchmark-case-builder-portable/install.sh --check
bash -n scientific-benchmark-case-builder-portable/install.sh
~~~

Close CF2 only when design, TOML, Dockerfile, drift, CaseSpec, eval, Packager and audit pass.

Close CF3 only when generated Local Candidate completes Harness, separate Verifier and Run Record while release remains blocked.

Keep CF4 Discovery, CF5 Diagnosis, CF6 Real Local Case and CF8 HPC Runtime pending.

## Definition of Done

- Adapter is repository-owned and protocol-tested.
- Builder generic mode stays portable.
- --target dftworld produces valid task.toml v1.2, Dockerfile, .dockerignore, target lock and factory state.
- Local/HPC fixtures pass CaseSpec, eval loader, Packager and Candidate audit.
- Generated bytes are deterministic and drift-detectable.
- Security/runtime fields cannot be injected through free-form text.
- Generated Local Draft completes Trusted Harness Candidate lifecycle and separate Verifier smoke without external model.
- No Draft or smoke writes benchmark_valid=true.
- Existing Builder and Infra regressions stay green with documented new counts.
- Dashboard reports CF2=PASS and CF3=PASS; later gates remain honest.

The next plan after P0 is Discovery Run plus Trusted Diagnosis, not Common Verifier or HPC Gateway work.

