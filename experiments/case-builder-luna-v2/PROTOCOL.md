# Luna v2 forward-test protocol (Discovery MVP skill evaluation)

Date: 2026-09-03. Governs one independent leave-one-out forward test of the
`build-scientific-benchmark-case` skill after the v2.2.0 Discovery-MVP
rework (WP0–WP5). Companion to `experiments/case-builder-luna-v1/EVALUATION.md`.

## What this test measures (and what it does not)

The subject of the test is the **skill**, not any particular model. The driver
model is chosen for cost and availability — GPT-5-Luna (or any comparably
cheap model at the same tier) is "the cheap labor," and the v1 run used Luna,
so v2 defaults to Luna for comparability. Swapping the driver model changes
the data point, never the protocol: the held-out setup, the forbidden inputs,
and the rubric below are the controlled variables. Results are reported as
(skill, driver-model) pairs.

The test asks one question: from a held-out source ecosystem, can the skill,
driven end-to-end in a single completion, produce a case that clears the L1
gate — `source → packageable Candidate → runnable Discovery → verifier
standard result → attributable failure`?

## Held-out design

- Held-out target: **case 032**. Its materials, reference, thresholds,
  hidden data, and gold files are sealed; the driver must not read them, and
  the evaluator must not feed them in as hints during construction.
- May be referenced as context: **031, 033, 034** (already-exposed cases).
- Also forbidden as input: the conclusions and fix list of this v2.2.0
  rework and of the Luna v1 evaluation narrative about *how to score*; the
  v1 failure taxonomy is encoded in tooling (fixtures and gates), which is
  fair game because that is precisely what is being tested.
- All outputs go to `experiments/case-builder-luna-v2/generated/` and
  nothing outside this tree may be written. Reads outside it are limited to
  the exposed sources 031/033/034 and the skill/package itself.

## Execution boundaries

- The run is `mode=mvp category=mlp` (per
  `references/common/mvp-runnable-draft.md`), one completion, no repair
  rounds. **First completion is frozen**; no score-driven retry. A failed
  gate is a data point, not a prompt to rerun.
- No network access, no package installation or fetch attempts (the v1
  unauthorized-uv-fetch defect stays a recorded violation class: attempt =
  violation), no container builds, no real HPC submission.
- Local, read-only tooling (repo `.venv`, `check_discovery_runnable.py`,
  the real packager in a temp dir) is authorized by default; anything that
  executes or mutates beyond that is not.

## Must-pass gates (all eight, from the completion output)

1. `check_discovery_runnable.py` exits 0 on the produced case (with
   `--repo-root` into this repository).
2. The semantic spec validators (`validate_spec.py`, `check_readiness.py`)
   were run as subprocesses with the repository interpreter — no
   YAML/Ruby syntax parse substituted (the report records the interpreter).
3. `real_packaging` passed: the **actual** `CaseSpec.load` +
   `package_candidate()` staged the Candidate; bundle agreement checked
   instruction/manifest/schema against the real bundle.
4. `verifier_mount_smoke` passed: `tests/test.sh` run in a faithful
   `/tests` + sealed-root + result-dir layout produced standard results.
5. Negative submissions are graded, not described: empty →
   `AGENT_FAILURE/NO_SUBMISSION`; forged/missing-model/broken-lineage →
   `AGENT_FAILURE/SCIENTIFIC_FAIL` with V-layer attribution in `reason`.
6. Every emitted `result.json` validates against the common result schema
   (six fields, no extras).
7. `benchmark_valid=false` throughout; no hand-asserted state —
   `runnable_draft` appears in `VALIDATION.json` only with the checker's
   `runnable_derivation` record.
8. Zero forbidden-path accesses or unauthorized package fetches/network use
   (self-reported and integrity-checked against the filesystem, as in v1).

A tree failing any gate is reported as failing at the gate name; the score
then reflects the partial-credit rubric without over-riding the gate.

## Scoring

Same rubric shape as v1 so the pair is comparable
(`experiments/case-builder-luna-v1/EVALUATION.md`):

| Area | Max |
|---|---:|
| Runnable-Draft mechanics | 25 |
| Scientific fidelity to held-out source | 25 |
| Verifier and anti-gaming | 20 |
| Execution/runtime realism | 15 |
| Evidence honesty/lifecycle | 10 |
| Maintainability/proportionality | 5 |

Targets: **total ≥ 80/100**, **Runnable-Draft mechanics ≥ 21/25**,
**Verifier ≥ 14/20**. The mechanics and verifier floors are the point of the
rework; a high total with a floored area is a failed experiment.

Scoring is done by an evaluator session that is *not* the driver, after the
completion is frozen, reading only the output tree plus the sealed 032
materials for the fidelity row.

## Deliverables of a run

- `generated/<case-id>/` — the case tree (frozen).
- `MVP-READINESS.json` — the checker report, verbatim.
- `RUN-LOG.md` — driver transcript summary: pauses taken, authorizations
  requested, boundary violations (if any).
- `EVALUATION.md` — scored evaluation with the gate-by-gate pass/fail table.
