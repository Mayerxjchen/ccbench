# Case Builder Luna v1 — evaluation plan

Planner: GPT-5.6 Sol
Builder under test: GPT-5.6 Luna
Date: 2026-09-03

## Question

Can a Luna agent, using `scientific-benchmark-case-builder-portable`, turn a
paper/source ecosystem into an honest, coherent Runnable Draft that is ready
for a real Discovery run, and how does that draft differ from long cases
031–034?

## Controlled design

Use a leave-one-case-out reconstruction.

- Held-out target/gold: `031-matclaw-cips-active-distillation`.
- Builder-visible source inputs: `benchmark/sources/matclaw/`, especially the
  paper PDFs, common CIPS structure/model assets, and `task1/manifest.json`.
- Builder-visible structural references: cases 032, 033, and 034.
- Builder instructions: the portable Skill package itself.
- Forbidden during construction: case 031, all evidence/job/run material for
  031, case 042, and reports that describe 031's implementation or 042's Skill
  gaps. The prohibition is prompt-enforced and recorded in the builder report.
- Output: `experiments/case-builder-luna-v1/generated/901-luna-cips-active-distillation/`.
  It is deliberately outside the repository root so benchmark discovery cannot
  mistake it for a released case.

The target is a `runnable_draft`, not a released benchmark. No network access,
container build, training, MD, remote mutation, or HPC submission is authorized.
Local read-only inspection, file construction, local unit/static checks, and
portable Skill validation scripts are authorized.

## Required builder artifacts

The generated tree must follow the Skill's directory contract and contain a
candidate-visible bundle contract, executable/dry-runnable task contract,
evidence-backed design/specification, verifier plan and implementation or an
explicitly justified Runnable-Draft substitute, fixture plan/tests, execution
profiles, planned reference/solution state, validation state, and
`benchmark_valid=false`.

The builder must also write outside the generated case:

- `BUILDER_REPORT.md`: actions, source paths read, commands/checks run, open
  gates, known limitations, and whether any forbidden path was accessed.
- `BUILD_PROMPT.md`: exact task prompt used for the Luna run.

## Evaluation rubric (100 points)

1. Runnable-Draft mechanics — 25
   - required tree and parseable contracts;
   - local tests/validators pass or failures are precisely attributable;
   - candidate bundle can be derived and audited;
   - a real Discovery invocation is specified without pretending it ran.
2. Scientific fidelity to the held-out 031 target — 25
   - active-distillation objective and termination criteria;
   - teacher/student/committee roles and independent train/test split;
   - explore/select/teacher-label/retrain lineage;
   - measurable deliverables and reproducible metrics.
3. Verifier quality and anti-gaming — 20
   - outcome-based checks, not expert-path matching;
   - hard prompt outcomes map bidirectionally to verifier layers;
   - positive, negative, and alternative-valid coverage;
   - provenance, leakage, and fabricated-artifact defenses.
4. Execution/runtime realism — 15
   - correct `hpc_controller` separation;
   - scheduler/artifact-fetch contract and resource profiles;
   - no local-only fiction for long-running training/MD;
   - unresolved runtime facts remain explicit blockers/plans.
5. Evidence honesty and lifecycle compliance — 10
   - no fabricated reference results, thresholds, or completed runs;
   - reference/solution maturity is honestly `planned` or `deferred`;
   - release state remains false and gates are fail-closed.
6. Maintainability and proportionality — 5
   - coherent authority for facts, limited duplication, readable structure,
     useful scripts/tests, and no gratuitous bulk.

## Comparison products

After Luna finishes, Sol evaluates without asking Luna to revise against the
gold. The report will include:

- gate/check results for the generated draft;
- a rubric score with evidence;
- a feature matrix for generated draft vs 031, 032, 033, and 034;
- quantitative inventory (files, code/test/document lines, public/hidden
  surface, verifier/tests/profile coverage);
- substantive differences from 031, separated into improvements, acceptable
  Runnable-Draft deferrals, and defects;
- observed Skill strengths/weaknesses and concrete changes recommended for the
  Skill.

No score-driven retry is allowed. If Luna hits an infrastructure/tool failure,
the failure is reported separately; otherwise the first completed construction
is the evaluated observation.
