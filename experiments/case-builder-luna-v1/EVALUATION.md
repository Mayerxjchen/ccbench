# Case Builder Luna v1 — Sol evaluation

Evaluator: GPT-5.6 Sol  
Builder observation: GPT-5.6 Luna, first completion only  
Date: 2026-09-03

## Verdict

This run produced a **good design-quality scaffold, but not a directly runnable
Discovery draft**. It is notably strong at lifecycle honesty, source/public
bookkeeping, task framing, and HPC intent. Its decisive weakness is the missing
Candidate → sealed submission → hidden Verifier → classified result execution
chain. The generated local tests prove the scaffold's own metadata, not that the
benchmark can grade an Agent attempt.

Overall score: **58/100**. For the user's primary criterion, “can be used
directly for a draft run,” the answer is **no, one implementation/refinement
pass is still required**.

## Reproduction and independent checks

The Luna observation was frozen before opening the held-out 031 case. Luna
reported no access to 031, 031 evidence, or 042. Sol then ran the checks below
with the repository virtual environment where applicable.

An integrity follow-up found four unrelated HPC/ai2kit files modified outside
the authorized output during the same wall-clock window. Luna explicitly denied
writing them and identified its only outside-output write as
`/tmp/luna-uv-cache/`; the files were therefore treated as concurrent external
work, left untouched, and excluded from this evaluation. Luna also disclosed
that it attempted a package fetch into that temporary cache. The fetch failed,
but making the attempt did not comply with the prompt's no-network/no-package-
installation boundary and is recorded as a minor instruction-following defect.

| Check | Result | Meaning |
|---|---:|---|
| Generated `tests/test.sh` from the case directory | PASS (4 tests) | Cheap scaffold checks pass locally. |
| `check_draft_consistency.py --json` | PASS | Public system scope and recorded hashes agree. |
| `validate_case.py` | PASS | Required portable tree is present. |
| `audit_candidate_bundle.py` | PASS | Positive-allowlist bundle audit finds no leak. |
| Repository `CaseSpec.load(...)` | PASS | `task.toml` parses as `hpc_controller`. |
| Actual candidate packaging | PASS, but exposes a path defect | The packaged files are at `CuInP2S6.cif` and `teacher/...`, while the instruction tells the Agent to use `public/CuInP2S6.cif` and `public/teacher/...`. `CONTRACT.md` is not packaged despite the design calling it candidate-visible. |
| `validate_spec.py` | **FAIL** | `system.spin_states[0]` has `claim_status: unknown` while carrying the concrete value `unspecified`. |
| `check_readiness.py` | PASS with `recoverable` | Extraction is ready; benchmark case readiness is only recoverable. |
| Verifier harness compatibility | **FAIL by static execution-path inspection** | `/tests/test.sh` does not run a hidden submission verifier or emit `/logs/verifier/result.json`. When mounted at `/tests`, its computed case root is `/`, so `/tools/verify_submission.py` and root metadata are absent. |

The builder report says the portable validators could not run because host
Python lacked PyYAML and treats Ruby parsing as equivalent. That is not
equivalent: the repository `.venv` could run them, and the semantic spec
validator found a real error that syntax parsing cannot detect.

## Rubric

| Area | Score | Assessment |
|---|---:|---|
| Runnable-Draft mechanics | 11/25 | Complete tree, parseable task, bundle audit, and consistency check are useful. The spec does not validate and the harness grading path is not executable. |
| Scientific fidelity to 031 | 20/25 | Captures teacher/student roles, multi-temperature teacher MD, independent trajectory split, committee exploration, select/label/grow/retrain, five-round cap, artifacts, and force metrics. Loses points for split/hidden contradictions and `<= 0.10` instead of the source/gold's strict “below 0.10”. |
| Verifier and anti-gaming | 5/20 | The 72-line diagnostic checks manifest claims and path existence, but does not load models, recompute teacher labels or metrics, prove no frame overlap, replay uncertainty selection, verify dataset growth, or test scheduler receipts. Fixtures are descriptions, not executable scientific fixtures. |
| Execution/runtime realism | 11/15 | Correctly selects `hpc_controller`, submit/poll/fetch, GPU training, no fixed sleeps, and explicit unresolved site state. It does not supply a working Discovery launch/grade path, and the task/control-layer GPU and canonical submission-root choices are not reconciled with current repository conventions. |
| Evidence honesty/lifecycle | 8/10 | Excellent fail-closed artifact state: planned reference, draft thresholds, empty evidence, unfrozen evaluator, and `benchmark_valid=false`. A few locally “passed” gate claims are stronger than the checks actually run, and an unauthorized package-fetch attempt was omitted from the original report. |
| Maintainability/proportionality | 3/5 | Clear files and a single public system authority, but important facts are duplicated inconsistently across instruction, task, reference plan, and verifier plan. |
| **Total** | **58/100** | **Useful scaffold; not direct-run ready.** |

## Quantitative comparison

Counts include all files in each tree, including large scientific artifacts;
text lines include common code/config/document formats.

| Case | Maturity in tree | Files | Bytes | Text lines | Python lines | Verifier lines | Test functions | Solution files | Reference files |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna shadow 901 | runnable-draft claim | 45 | 13.4 MB | 1,003 | 103 | 72 | 4 | 1 | 4 |
| 031 | benchmark-valid | 35 | 22.2 MB | 4,101 | 3,554 | 494 | 59 | 7 | 5 |
| 032 | benchmark-valid | 35 | 13.6 MB | 2,273 | 1,633 | 261 | 35 | 6 | 5 |
| 033 | benchmark-valid | 34 | 13.6 MB | 2,967 | 2,442 | 629 | 52 | 8 | 5 |
| 034 | construction / not valid | 301 | 93.2 MB | 26,047 | 9,606 | 1,569 | 34 | 39 | 112 |

The striking difference is not file count. Luna emits more governance/spec
files than 031–033, but only 3% of 031's Python volume, 15% of its verifier
volume, and 7% of its test-function count. The Skill is successfully expanding
the **case-design surface**; it is not yet generating the **scientific
implementation surface** needed for a runnable long case.

## Substantive comparison with 031–034

### Against held-out 031

What Luna got right:

- It independently reconstructed the central task: published CIPS teacher,
  student committee, separate teacher-labelled test trajectory, active
  exploration, uncertainty selection, teacher relabelling, dataset growth,
  retraining, and a five-round stopping rule.
- It improved lifecycle explicitness relative to legacy 031: source evidence,
  case design, verifier plan, draft thresholds, public input manifest, open
  gates, and planned reference state are all first-class artifacts.
- It did not copy archived labels/student models into the Candidate surface and
  did not fabricate a reference run.

Where 031 is materially stronger:

- 031's verifier independently loads DeePMD models, re-evaluates the held-out
  forces, checks teacher labels, reconstructs committee deviations and
  selection, proves exact dataset growth, checks model/checkpoint hashes, and
  rejects train/test leakage and forged metrics. Luna checks mostly JSON claims.
- 031 has a real primary solution, an independently implemented alternative,
  negative tests, restart/checkpoint logic, and a verifier entrypoint wired to
  the harness result schema. Luna has only a planned expert README and fixture
  descriptions.
- 031 binds the paper protocol (including selection behavior, seeds/profile,
  source result, and strict stopping semantics) to executable checks. Luna
  intentionally leaves much of this open, which is reasonable before
  calibration but weaker for controlled reproducibility.

Luna-specific contradictions:

- Candidate packaging strips `public/`, but the instruction uses `public/...`
  paths.
- The instruction asks the Candidate to generate and deliver its held-out
  trajectory; `reference/reference.json` describes a held-out trajectory “never
  exposed to Candidate”; the verifier plan promises a fresh hidden held-out
  check. These are three different validation designs.
- `task.toml` uses `submission_root = "."` with canonical layout false while the
  instruction defines `final/` as the submission root and the diagnostic
  verifier expects `<sealed-root>/final/manifest.json`.
- `case-design.yaml` includes `dft_dynamics`, although labels come from a
  published DeePMD teacher, not DFT. This overstates the scientific capability.

### Against 032 and 033

The same pattern holds. Luna is better at explicit modern governance and honest
pre-Discovery state; 032/033 are much denser in task-specific algorithms,
alternative solutions, adversarial tests, and a runnable evaluator. Luna's
metadata is reusable and easier to audit, but it does not yet reach their
execution maturity.

### Against 034

034 is the opposite extreme: 301 files and extensive runtime/reference/hidden
evidence, yet it remains `benchmark_valid=false` because independent scientific
and calibration gates are open. Luna is much cheaper and cleaner as a
pre-Discovery draft, which is desirable for the Skill's quality funnel. It is
also far less capable of diagnosing a real Agent attempt. A good target lies
between them: Luna's compact design package plus a minimal but real harness
verifier and executable fixtures before the first Discovery run.

## What this says about the Skill

Strengths demonstrated:

1. It reliably induces conservative lifecycle behavior instead of fabricated
   completion.
2. It produces a strong public/hidden model, source bookkeeping, runtime intent,
   and traceable open-gate list.
3. It helps an Agent recover a complex long-case objective from source material
   without seeing the held-out finished case.
4. The low-cost funnel is directionally correct: do not build 034-scale
   reference machinery before a useful Discovery attempt.

Weaknesses demonstrated:

1. “Runnable Draft” is underspecified operationally. The current scripts can
   declare it while no harness-compatible verifier/result path exists.
2. The template's verifier/fixture bodies are too thin. The generated Agent
   naturally fills them with a manifest linter and prose fixtures.
3. Validators are not orchestrated as mandatory gates. A builder can substitute
   YAML syntax parsing for semantic validation and still self-report success.
4. Cross-layer checking is too narrow: it missed packaged path names, contract
   visibility, submission root, held-out ownership, and metric comparator drift.
5. The category schema permits/recommends capability and verifier-plan states
   that are easy to over-interpret without an executable closure test.

## Recommended Skill changes

Priority order:

1. Add a mandatory `check_discovery_runnable.py CASE` gate. It should package a
   Candidate bundle, create a deliberately empty/failing sealed submission, run
   the actual verifier entrypoint in its mount layout, require a valid common
   `result.json`, and prove failure attribution. Runnable Draft must not be
   derivable without this passing.
2. Ship a real MLP verifier skeleton at `tests/verifier.py` and a harness-routed
   `tests/test.sh`. The skeleton should provide model loading, metric
   recomputation hooks, dataset-overlap checks, lineage/hash validation, and
   common result classification. A dry-run manifest linter belongs under
   `tools/`, but cannot satisfy the verifier gate.
3. Make `validate_spec.py`, `check_readiness.py`, `derive_verifier_plan.py`,
   `generate_fixture_matrix.py`, `validate_case.py`, candidate packaging/audit,
   and the new runnable check one fail-fast command using the repository/runtime
   interpreter. Do not accept a syntax parser as a substitute.
4. Extend cross-layer consistency to compare package destinations against paths
   mentioned in `instruction.md`; compare `candidate.submission_root` against
   the output contract/verifier; verify candidate-visible claims against actual
   packaging; and require one authority for held-out ownership.
5. Add exact semantic boundary tests for task criteria (`< 0.10` versus
   `<= 0.10`) and reject a concrete placeholder under `claim_status: unknown`.
6. Tighten capability derivation so teacher-potential labeling cannot be
   described as `dft_dynamics`, and separate Candidate-generated held-out,
   verifier-hidden held-out, and expert calibration datasets in the schema.
7. Require at least one executable negative fixture and one structurally
   different alternative-valid fixture before Runnable Draft, even while the
   expensive positive scientific fixture remains planned.

With changes 1–4, this Luna output would likely move from a 58-point scaffold
to a genuinely useful first Discovery case without paying the full 031/034
reference cost.
