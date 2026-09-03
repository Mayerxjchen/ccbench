# Luna builder report

## Result

Created the experimental Runnable Draft at
`generated/901-luna-cips-active-distillation/`. Its maturity is
`runnable_draft`; `benchmark_valid.json` is explicitly false and no release
state was asserted. The draft includes the public CIPS structure and teacher
bundle, complete candidate instruction/task/runtime contracts, MLP evidence
map and reproduction spec, readiness assessment, verifier-layer plan,
fixture matrix, scheduler profiles, provenance-aware draft verifier, and
fail-closed reference/evidence manifests.

## Source/reference directories inspected

- `/Users/xjchen/bench/mlffbench/benchmark/sources/matclaw/` (README,
  inventory, acceptance/recovery/source locks, task1 manifest, teacher
  provenance/runtime locks, CIPS structure and teacher assets, and Task 1
  reports/history metadata).
- `/Users/xjchen/bench/mlffbench/032-matclaw-cips-curie-temperature/` (case
  shape, task/instruction, profiles, validation/evaluator contracts, tests).
- `/Users/xjchen/bench/mlffbench/033-matclaw-cips-domain-wall-search/` (case
  shape, task/instruction, profiles and verifier-oriented tests).
- `/Users/xjchen/bench/mlffbench/034-ai2kit-water64-end-to-end-potential/`
  (contract, task/instruction, reference threshold shape and end-to-end
  verifier/fixture conventions).
- `/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/`
  Skill, Common Core policies, MLP policies/templates, and local scripts.

The CIPS PDFs were locally inspected only through available source metadata
and recovered Task 1 reports; no network access was used. Source evidence
records the recovered paper/repository/model identities and leaves unresolved
runtime/calibration facts explicit.

## Commands and validators

- Copied the Common Core, HPC-controller, and MLP template overlays into the
  generated case, then authored the case files and public inputs. Outcome:
  complete required tree, with no training/MD/labeling/container/HPC action.
- `ruby -e 'require "yaml"; ... YAML.load_file(...)'` over all authored YAML.
  Outcome: all authored YAML parsed successfully.
- Bundled Python 3.12 `tomllib` and `json` parse over `task.toml` and all JSON
  contracts. Outcome: TOML and JSON parsed successfully.
- `python3 .../check_draft_consistency.py CASE --json`. Outcome: `valid: true`,
  canonical scope `cips_bulk_3x3x1`, no hash mismatches or scope errors.
- `python3 CASE/tools/verify_submission.py --dry-run`. Outcome: valid; identity,
  path, model, lineage, split, and finite-metric checks listed.
- `bash CASE/tests/test.sh`. Outcome: 4 local unittest checks passed; the
  dry-run verifier passed.
- SHA-256 over public inputs. Outcome: structure, teacher graph, and type-map
  hashes match the recovered source lock; no public symlinks were found.
- Portable `init_case.py`, `validate_case.py`, `validate_spec.py`,
  `check_readiness.py`, `audit_candidate_bundle.py`, and `check_release.py`
  were attempted where useful. They could not start because the host Python
  lacks PyYAML and network/package installation is not authorized. This is an
  environment limitation, not a scientific execution result; Ruby YAML and
  the dependency-free MLP consistency checker supplied equivalent local
  syntax/scope checks. `check_release.py` therefore remains intentionally
  unproven and would fail closed on the planned reference/draft thresholds.

## Open gates and maturity

Open gates are G2, G4, G5, G6, G7, G8, G9, G10, G11, and G12. G0/G1 source and
candidate-boundary checks are locally evidenced; runtime qualification is not
complete. Specifically open are the site/runtime receipt, smoke and formal
scheduler runs, expert reference and independent parser, hidden-set
generation/inaccessibility, threshold freeze, fixture execution, sealed
restorable evidence, independent rerun, and final release derivation. The
reference is `planned`, thresholds are `draft`, evaluator manifest is not
frozen, and solution is `planned`.

## What a user must do for the first Discovery run

1. Provide/qualify a real GPU scheduler, `matclaw-cips` runtime, artifact-fetch
   capability, image digest, and credentials; record the site receipt in the
   HPC profiles and runtime lock.
2. Run the smoke profile through the real scheduler, poll to terminal state,
   fetch outputs, and retain receipts. Resolve any runtime/resource blocker.
3. Launch a fresh Candidate with only `public/`, `instruction.md`,
   `task.toml`, and `CONTRACT.md`. The Candidate must execute teacher MD,
   committee training/exploration, uncertainty selection, teacher labeling,
   dataset growth, retraining, and independent held-out evaluation, then
   fetch a complete `final/` bundle.
4. Store a durable Discovery record (source commit, candidate digest, runtime
   identity, job IDs, logs/artifacts, and failure code), classify it with the
   Skill failure taxonomy, and decide REJECT/REFINE/PROMOTE before investing in
   expert reference/calibration work.

## Forbidden-path audit

None. No forbidden path was accidentally accessed: case 031, 031 evidence,
case 042, and reports/history describing those implementations were not
inspected.
