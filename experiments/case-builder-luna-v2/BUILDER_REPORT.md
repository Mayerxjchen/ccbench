# BUILDER_REPORT — Luna v2 forward test (case-builder-luna-v2)

Driver session: builder agent, mode=mvp, category=mlp, per
`BUILD_PROMPT.md`. Case produced at
`experiments/case-builder-luna-v2/generated/902-mvp-cips-curie-temperature/`.

## 1. Skill invocation

`/build-scientific-benchmark-case mode=mvp category=mlp` (explicit invocation
via BUILD_PROMPT). SKILL.md read from disk at
`/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`
and the mvp lifecycle worked: intake → extract-spec → design → scaffold →
minimal construct → MVP gate. Routed references loaded in full:
`references/category-registry.yaml`, all `references/common/*` (lifecycle,
cross-layer consistency, discovery refinement, maturity model, failure
taxonomy, public/hidden boundary, prompt contract, packager, evidence
retention, runnable-draft) and all `references/categories/mlp/*`
(workflow-capabilities, verifier-policy, readiness-policy, prompt-contract,
target-model-policy, reproduction-schema, source-evidence-policy). All
`scripts/**` (both `common/` and `categories/mlp/`) and the whole
`assets/case-template/**` were read; the mvp-passing regression fixture under
the package's `tests/fixtures/` was inspected for shape.

## 2. Source / reference directories inspected (complete list)

- `/Users/xjchen/bench/mlffbench/benchmark/sources/matclaw/` — README.md,
  inventory.json, source.lock.json, acceptance.json, recovery_gate.json,
  task2/manifest.json, task1/manifest.json, task3/manifest.json (task3 for
  boundary identification only), common/CuInP2S6.cif,
  common/teacher-model/{frozen_model.pb, type_map.raw, recovery.json,
  CIPS_data.zip (listed)}, teacher-runtime.lock.json,
  teacher-runtime-requirements.txt, validate_teacher_model.py,
  paper/matclaw-2604.02688v3.pdf (title/metadata + task-2 text via pypdf),
  repository/release/workspace_demo2a_curie_no_convergence/analysis/cips_tc_report.md,
  repository/release/workspace_demo2b_curie_with_convergence/{cips_tc_report.md,
  sweep/sweep_manifest.csv, results/cips_tc_data.csv, pilot/*.csv|md}
  (plus `.prompts.yaml` of both task-2 workspaces for protocol context),
  repository/release/remote_jobs/_efield_calculator.py (task boundary only).
- Reference case shapes (allowed): `/Users/xjchen/bench/mlffbench/031-matclaw-cips-active-distillation/`,
  `/Users/xjchen/bench/mlffbench/033-matclaw-cips-domain-wall-search/`,
  `/Users/xjchen/bench/mlffbench/034-ai2kit-water64-end-to-end-potential/`
  (task.toml conventions: [hpc] contract_version hpc-execution/v1,
  qualification requires, [runtime] family matclaw-cips ==2.2.11).
- Portable Skill package (code, templates, references, tests/fixtures).
- Repo contracts needed for real packaging: `dftworld_bench/contracts/case.py`,
  `dftworld_bench/core/packager.py`, `/Users/xjchen/bench/mlffbench/schemas/case.schema.json`,
  repo `README.md`, `python -m dftworld_bench.hpc help` (runbook commands).

## 3. Disclosure — borderline source content

`benchmark/sources/matclaw/acceptance.json` (inside the explicitly ALLOWED
sources directory) contains per-case acceptance target values labeled
"031"/"032"/"033", including 032's Tc target (261.3 K), a 10.0 K window, the
13-temperature grid, and atom count. I read this file while inventorying the
allowed directory. I did not read any 032 case directory, evidence/ path,
luna-v1 path, 042, or docs/superpowers/plans, and ran no git-log search. The
case's provisional draft window is attributed exclusively to the upstream
demo2b report (`ev-workspace-2b-report`: "261.3 +/- 10.0 K" self-consistency),
which I had read before this file; the instruction/public bundle carry no
Tc value, grid, or threshold; `reference/thresholds.json` stays `draft` with
`thresholds: {}` and an explicit not-frozen note.

## 4. Commands and validators run (outcomes)

All with `/Users/xjchen/bench/mlffbench/.venv/bin/python`.

| step | command | outcome |
|---|---|---|
| scaffold | `init_case.py --category mlp --kind published_model_execution --design designs/curie-case-design.yaml --output .../902-mvp-cips-curie-temperature` | valid=true, case_status=draft, benchmark_valid=false |
| extract-spec semantics | `validate_spec.py source/mlp-reproduction-spec.yaml --evidence source/source-evidence-map.yaml` | OK |
| readiness | `check_readiness.py ... --output source/reproducibility-assessment.yaml` | extraction ready, execution ready, verification not_applicable, benchmark recoverable (unknown licenses); no blocked |
| source locks | `hash_sources.py --file x17 --identity x3 --output source/sources.lock.json` | 20 sources locked |
| verifier plan | `derive_verifier_plan.py --design case-design.yaml --output verifier-plan.yaml` | layers MLP-V0,V1,V2,V6,C-V7,C-V8; fixtures 1/1/1 |
| fixture build | helper `scripts/build_fixtures_902.py` (generated 4 negatives + structural positive + alternative-valid with real sealed-model bytes) | generated |
| structural | `validate_case.py CASE` | PASS |
| cross-layer | `check_draft_consistency.py CASE --json` | valid=true, errors=[], canonical system `cips-monolayer-6x6x1` |
| fixture closure | `generate_fixture_matrix.py CASE --json` | valid=true (positive 1/1, negative 4/1, alt 2/1) |
| manifest lint | `tools/validate_submission_manifest.py` on all 5 fixture dirs | 3 valid submissions lint OK; forged & broken-lineage fail exactly as designed |
| verifier self-test | `pytest CASE/tests/test_verifier_contract.py -q` | 10 passed |
| pre-gate mount smoke | helper `scripts/pre_gate_smoke_902.py` (mirrors the gate's 7 smoke cases incl. stripped verifier.py) | ALL PASS |
| packaging pre-check | inline `CaseSpec.load` + `package_candidate` in temp dir | loads hpc_controller spec; bundle = instruction.md + public/** (no CONTRACT.md, prefix kept) |
| **L1 gate (single execution)** | `check_discovery_runnable.py CASE --repo-root /Users/xjchen/bench/mlffbench --output CASE/MVP-READINESS.json --derive-state` | **mvp_runnable=True, exit 0, state=runnable_draft** |

Not run: the portable package's own pytest suites (`test_luna_v1_regressions.py`
and others) because they reference/import the forbidden
`experiments/case-builder-luna-v1/` path; avoiding access outweighed
regression coverage. No network, installs, containers, training, MD, or HPC
submissions were performed.

## 5. MVP gate result (verbatim)

Console:

```text
mvp_runnable=True state=runnable_draft
```

`MVP-READINESS.json` (full text as written by the gate): all 9 checks
`pass` (`validate_case`, `category_semantics`, `verifier_plan`,
`fixture_matrix`, `cross_layer_consistency`, `real_packaging`,
`bundle_agreement`, `verifier_mount_smoke`, `pre_discovery_honesty`),
`blocking_errors: []`, `benchmark_valid: false`, and
`deferred_release_work`:
- MLP-V4/V5/V6 hidden scientific verification
- expert reference run and independently reproduced lineage
- double-threshold calibration freeze
- scientific positive/alternative-valid fixture closure
- G0-G12 release gate closure and evidence retention

(Report file: `generated/902-mvp-cips-curie-temperature/MVP-READINESS.json`.)

## 6. Maturity state and open gates

- `case_status: runnable_draft` — derived only by the gate
  (`VALIDATION.json#runnable_derivation.derived_by =
  check_discovery_runnable.py`, mvp_runnable true, report MVP-READINESS.json).
- `benchmark_valid: false` (never asserted otherwise anywhere).
- Open gates: G0-G12 all open (draft→L1 leaves every release gate open).
  The material ones: G5/G6 expert reference + independent verification
  (blocked by the missing upstream trajectories → regeneration required),
  G7/G8 threshold double-calibration freeze, G9 formal reference runs (site),
  G10 Discovery runs + classification, V6 hidden comparison data.

## 7. What a user must do to launch the first real Discovery run

Per `DISCOVERY-RUNBOOK.md` (case root):
1. Qualify the site/runtime: `dispatcher.gpu` and `runtime.matclaw-gpu`
   (repository qualification driver `scripts/infra/qualify_hpc_dispatcher.py`;
   runtime family matclaw-cips == DeePMD-kit 2.2.11 per task.toml), then
   freeze `reference/compute-runtime.lock.json` with the real image digest.
2. Authorize compute: stage the bundle via the real packager (already proven
   locally), run the agent task via the repo harness (`eval.py` runner),
   MD jobs through `bench hpc submit JOB_FILE OPERATION_ID ATTEMPT` →
   `status`/`fetch`. First run must be the **smoke** profile (pilot subset,
   plumbing only) — never formal.
3. Grade the sealed submission with the mounted `tests/test.sh` →
   `result.json` (layout identical to the L1 mount smoke), classify with the
   failure taxonomy (`classify_failure.py`), and record artifacts under
   `evidence/` per the retention policy.
4. Only after reference regeneration + double calibration may the formal
   profile and V6 hidden comparison exist.

## 8. What was created (summary)

Case tree `generated/902-mvp-cips-curie-temperature/`: case-design.yaml,
task.toml (hpc_controller, matclaw-cips runtime, qualification requires),
CONTRACT.md (hidden), instruction.md (paper-faithful, no answer/threshold
leak), public/ (CIF + sealed frozen model + type_map + system.json +
input-manifest.json + submission-schema.json, all hash-locked),
verifier-plan.yaml (V0/V1/V2/V6/C-V7/C-V8), tests/ (case-adapted
verifier.py with sealed model-authenticity V2 and C-V8 manifest contract;
test.sh unchanged; hidden/teacher-digest.json; 6 executable fixtures;
adapted self-test), source/ (reproduction spec, evidence map with one
noncritical unresolved conflict recorded, readiness assessment,
sources.lock.json from `hash_sources.py`, plus the template's
`source/source.lock.json` left as the empty stub it shipped as — superseded
by `source/sources.lock.json` and `reference/source.lock.json`; untouched
after the gate per the single-completion rule), reference/ planned+draft
stubs with an independent `source.lock.json`, execution hpc overlay stubs, profiles
(smoke/formal draft), solution/expert plan, evidence stubs,
DISCOVERY-RUNBOOK.md, MVP-READINESS.json, VALIDATION.json (runnable_draft),
benchmark_valid.json (false). Helpers outside the case:
`scripts/build_fixtures_902.py`, `scripts/finalize_locks_902.py`,
`scripts/pre_gate_smoke_902.py`, `designs/curie-case-design.yaml`.

## 9. Forbidden paths accidentally accessed

**none.** No read/stat/list/search of `032-matclaw-cips-curie-temperature/`,
any `evidence/` path concerning 032, `042-go-water-dpmp/`,
`experiments/case-builder-luna-v1/`, or `docs/superpowers/plans/`; no git
history inspection of those. The only 032-adjacent content encountered was
inside the explicitly allowed `benchmark/sources/matclaw/` directory
(`acceptance.json`), disclosed in section 3.
