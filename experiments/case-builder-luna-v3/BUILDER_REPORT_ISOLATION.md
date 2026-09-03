# Builder Report — Isolation Regime (case 906)

Run: clean-context forward evaluation of `build-scientific-benchmark-case`
v2.3.0 (branch `case-builder/mvp-20260903`, commit `1fe2d3d`), worktree
`/Users/xjchen/bench/.wt/case-builder-mvp-v3/`. Invocation:
`/build-scientific-benchmark-case mode=mvp category=mlp`. Regime: **isolation**
(authorized source = `materials-clean/` only).

Case produced: `generated/906-mvp-ljcluster-model-eval/case/`

## 1. Sources inspected

Authorized source ecosystem (`materials-clean/`), read in full:
- `materials-clean/README.md`
- `materials-clean/paper/paper.md`
- `materials-clean/repo/README.md`
- `materials-clean/repo/config.yaml`
- `materials-clean/repo/weights/lc-mlp-v1.txt`
- `materials-clean/data/dataset.csv`
- `materials-clean/data/README.md`
- `materials-clean/LICENSE-NOTES.md`

Skill package under test (worktree), read:
- `skills/build-scientific-benchmark-case/SKILL.md`
- `references/category-registry.yaml`
- `references/common/` — case-standard, lifecycle-and-gates, mvp-runnable-draft,
  public-hidden-boundary, reference-and-solution-policy, verifier-and-fixture-policy,
  cross-layer-consistency, execution-classes, discovery-and-refinement
- `references/categories/mlp/` — prompt-contract, verifier-policy,
  source-evidence-policy, reproduction-schema, readiness-policy,
  target-model-policy, workflow-capabilities
- `scripts/common/` — check_discovery_runnable.py, init_case.py, validate_case.py,
  generate_fixture_matrix.py
- `scripts/categories/mlp/` — validate_spec.py, check_readiness.py,
  hash_sources.py, derive_verifier_plan.py, check_draft_consistency.py
- `assets/case-template/` (common + categories/mlp) — templates and fixtures

dftworld runtime code imported by the gate (via `--repo-root`, inspected for
contract only): `dftworld_bench.contracts.case.CaseSpec`,
`dftworld_bench.core.packager` (package_candidate, leak-scan, dest rules).

`repo/README.md` references `expected.json` as grader-side; that file is absent
from `materials-clean/` (excluded by construction) and was never read. The
planted `paper/acceptance.json` is likewise absent from the clean view.

## 2. Commands / validators run (all with `/Users/xjchen/bench/mlffbench/.venv/bin/python`)

- `init_case.py --category mlp --kind model_evaluation --design … --output …/case --allow-blocked`
  → scaffold valid (`valid: true`).
- `derive_verifier_plan.py --design …/case/case-design.yaml --output …/verifier-plan.yaml`
  → layers V0,V1,V2,V4,C-V7,C-V8 all `selected`, `deferred_layers: []`.
- `validate_spec.py source/mlp-reproduction-spec.yaml --evidence source/source-evidence-map.yaml`
  → `OK` (rc 0).
- `check_readiness.py … --output source/reproducibility-assessment.yaml` → rc 0;
  readiness: extraction ready, execution not_applicable, verification ready,
  benchmark recoverable (model license unknown — honest, not blocked).
- `hash_sources.py --dir paper/repo/data --file license --exclude acceptance.json
  expected-output.json expected.json held-out-targets.json scores.json
  --output source/sources.lock.json` → rc 0; omissions recorded under `"excluded"`.
- `validate_case.py CASE` → rc 0 (pass).
- `check_draft_consistency.py CASE` → rc 0 (pass; invariant H green).
- `generate_fixture_matrix.py CASE` → `OK` (positive 1, negative 8, alt-valid 1).
- `pytest tests/test_verifier_contract.py` → 13 passed.
- `check_discovery_runnable.py CASE --repo-root /Users/xjchen/bench/mlffbench
  --output CASE/MVP-READINESS.json --derive-state` → rc 0.

One fix during build: `task.toml` initially staged only `public/**`; since
`case-design` declares `contract.candidate_visible: true`, added the
`CONTRACT.md` staging rule. Validators then passed.

## 3. MVP gate result (verbatim)

```
mvp_runnable=True state=runnable_draft
```
Exit code 0. All nine checks pass: `validate_case`, `category_semantics`,
`verifier_plan`, `fixture_matrix`, `cross_layer_consistency`, `real_packaging`,
`bundle_agreement`, `verifier_mount_smoke`, `pre_discovery_honesty`.
`blocking_errors: []`. `benchmark_valid: false`.

The mount smoke graded the four v3 integrity probes with exact attribution
(missing-artifact, multi-hash-mismatch, missing-plus-mismatch,
type-garbage-manifest) plus empty/forged/missing-model/broken-lineage negatives
and the structural positive; every `result.json` validates against the common
schema. `VALIDATION.json` now carries `case_status: runnable_draft` with
`runnable_derivation.derived_by: check_discovery_runnable.py` — the state was
written only by the gate's `--derive-state`, never by hand.

## 4. Maturity state and open gates

- `case_status: runnable_draft` (derived by the L1 gate).
- `benchmark_valid: false` everywhere (no `benchmark_valid=true` present).
- Open gates: G0–G12 remain open (draft-open set from scaffold). Deferred
  release work (per MVP-READINESS): MLP-V4/V5/V6 hidden scientific verification;
  expert reference run + independently reproduced lineage; double-threshold
  calibration freeze; scientific positive/alternative-valid fixture closure;
  G0–G12 release-gate closure and evidence retention.
- Reference state: `planned`; thresholds: `draft`. No executed reference,
  calibration, or hidden science is claimed.

## 5. To launch the first real Discovery run

Use the smoke-class profile (never `formal`), `local_sandbox` class, and only a
separately authorized runtime — runnable files are not permission to run.
1. Package the Candidate bundle with the real packager
   (`package_candidate(CaseSpec.load(CASE), dest)`).
2. Launch the Agent against the bundle under `profiles/smoke.yaml`.
3. Seal the submission; run `SUBMISSION_ROOT=<sealed> RESULT_DIR=<logs/verifier>
   BENCH_RUN_ID=<id> bash tests/test.sh`.
4. Record the run durably and classify with `classify_failure.py` +
   `references/common/failure-taxonomy.yaml`; record REJECT/REFINE/PROMOTE.
Full steps in `case/DISCOVERY-RUNBOOK.md`.

## 6. Forbidden-path accesses

`none`. No numbered case directories, `evidence/`, `docs/`,
`case-builder-luna-v1|v2/`, `materials/` (superset), `generated/` prior runs, the
main-checkout skill copy, or git history were inspected. No network access, no
package install/fetch, no container build, no training/MD/DFT, no remote
mutation, no HPC submission.
