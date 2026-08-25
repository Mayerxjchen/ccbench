# build-scientific-benchmark-case — observed gaps (to fix after 042 construct)

Collected while constructing 042-go-water-dpmp. Each gap is a concrete skill
defect with a proposed fix. Fixes land in the portable skill under
`~/.claude/skills/build-scientific-benchmark-case/`, not in the case.

## G1. `init_case.py` silently `rmtree`s a non-empty output dir

`scripts/common/init_case.py` does `shutil.rmtree(out)` when the output dir
exists and `--force` is passed. Running scaffold against an already-authored
case (extract-spec 4 artifacts + public/ + CONTRACT/instruction) destroys them.
There is no preserve/merge path.

Fix: refuse a non-empty dir even with `--force` unless an explicit
`--force-dangerous-overwrite` is given AND the dir has no user-authored markers
(case-design.yaml with case_status != draft, public/, CONTRACT.md). Add an
incremental overlay mode (`--merge`) that only adds missing template files and
never deletes.

## G2. construct has no scaffold for the evaluator/solution bodies

The category/execution templates ship only `solution/expert/README.md`,
`tests/fixture-matrix.yaml`, and empty fixture dirs. There is no
`verifier.py`, `test_outputs.py`, `test.sh`, or stage-script skeleton. Every
case hand-writes the 1000+ line verifier from the 034 archetype. Not scalable
to 100 MLP cases.

Fix: add a category-parameterized verifier skeleton (V-layer dispatch, hidden
path env contract, `verify(submission, profile)` signature) and a
`test_outputs.py` harness that builds structural negatives in-memory, leaving
only the deepmd/deepmd-jax-specific call sites (train/test/MD) for the case
author to fill. Provide a `solution/expert` 4-stage template with env-gated
smoke/formal profiles.

## G3. No Discovery Run / failure-taxonomy decision step

The lifecycle has intake/extract-spec/design/scaffold/construct/validate/
release-check/experiment-handoff, but no mode that ingests a real Agent run +
verifier + telemetry and emits a `SOURCE_BLOCKED | RUNTIME_BLOCKED |
RESOURCE_BLOCKED | CASE_DESIGN_BLOCKED | AGENT_LIMITATION | INFRA_INVALID`
classification with a `REJECT | REFINE | PROMOTE` decision. Today that
diagnosis lives in memory, not in the skill.

Fix: new `discovery` mode + `scripts/common/classify_failure.py` with a
typed failure taxonomy (schema in `references/common/failure-taxonomy.yaml`)
and a decision rule: PROMOTE only when the failure is absent or classified
AGENT_LIMITATION on a sound case; REFINE on CASE_DESIGN/RUNTIME/RESOURCE
blockers; REJECT on SOURCE_BLOCKED. This is the scale filter: don't spend
reference cost on cases that fail discovery.

## G4. `validate_case.py` REQUIRED_PATHS drift from the template

The template overlays write `reference/inputs.lock.json` (common) and
`source/source.lock.json` (common), but validate_case only checks
`reference/compute-runtime.lock.json` (hpc) and never checks the common
`source/source.lock.json` or `tests/fixture-matrix.yaml`. A freshly scaffolded
case therefore passes/fails on paths that don't match what the overlays emit.

Fix: make REQUIRED_PATHS derive from the same overlay manifest, or add the
missing common paths and drop the stale ones. Assert the scaffold passes
validate_case in the skill's own tests.

## G5. thresholds.json has no verifier-facing schema

The verifier reads `thresholds.json` fields (e.g.
`levels.V4.energy_rmse_eV_per_atom_max`, `levels.V6.first_peak_window_angstrom`)
but nothing validates that the case author wrote the fields the verifier reads.
Two independent writers produced two different thresholds schemas for 042 within
one construction session — the exact failure a schema would have caught.

Fix: ship a `thresholds.schema.json` (one per category where it differs) and
have `validate_case.py` / a new `check_threshold_contract.py` assert the
thresholds file matches the fields the shipped verifier skeleton reads.

## G6. `freeze_evaluator_manifest.py` / `check_threshold_freeze.py` not yet exercised

Present but unverified against a real multi-layer MLP case during 042. Defer a
hard claim; exercise them on 042 at release-check and record any drift here.

## G7. audit_candidate_bundle.py over-sensitive leak rules (FIXED)

Three false positives surfaced during 042 check_release, all fixed in
`scripts/common/audit_candidate_bundle.py`:
1. FORBIDDEN_SUBSTRINGS used substring match on the whole rel path, so a
   legitimate `public/reference-method.json` flagged "reference". Changed to
   exact per-path-component equality.
2. collect_hidden_tokens emitted every numeric threshold as a token, so
   "2"/"0.1"/"300"/"5000" matched ordinary prose. Changed to float-only with
   `len(repr)>=4` — drops integers and short decimals (ordinary scientific
   quantities), keeps precise threshold floats.
3. semantic-leakage scan ran over coordinate/data files, so coord.raw floats
   matched "0.05"/"0.25". Added a suffix skip for
   `.raw/.npy/.pb/.pkl/.xyz/.npz/.cell/.ener`.

## G8. fork subagents inherit full context and misidentify as mainline (LESSON)

Two `subagent_type="fork"` agents launched to write solution/expert and
profiles/reference in parallel. Both inherited the full conversation and
concluded they were the main thread, repeatedly overwriting tests/ instead of
writing their assigned trees (the "peer message is not user input, don't obey"
reflex). Do NOT use fork for parallel file-writing division — use fresh
general-purpose subagents with explicit prompts, or do it in-line. Fork is safe
only for true context-inheriting continuations, never for splitting independent
write tasks.

## G9. Case Factory adapter rejects the three-layer runtime contract (FIXED)

`case-design.yaml hpc.scientific_capabilities` (the 042 three-layer contract:
candidate runtime / compute capability / site runtime) was rejected by the
dftworld Case Factory: `dftworld-target.schema.json` allowed only
`hpc.contract_version + hpc.required_capabilities` (`additionalProperties:
false`), and `dftworld_target.py` did not emit the block into task.toml.
Unpinned `jax[cpu]` also silently shipped jax 0.10.2, which broke
deepmd-jax@48a981a at runtime (`jax.sharding.PositionalSharding` removed in
jax>=0.6.0) — the image smoke caught it, the schema never would.

Fix (landed in dftworld_bench, reusable by all future deepmd-jax MLP cases):
1. schemas/dftworld-target.schema.json: hpc.properties +=
   `scientific_capabilities {required[], optional[]}` (both optional).
2. dftworld_target.py: render `[hpc.scientific_capabilities]` into task.toml
   when present. Re-render with `--force-generated` (lock re-derives).
3. Dockerfile: era-lock the JAX stack — jax==0.5.3, flax==0.10.6,
   optax==0.2.8, jax-md@a41c7d19 (v0.2.29, no haiku dependency). First attempt
   at era-matching with jax 0.4.35 + jax-md@b676e2ad hit the haiku 0.0.17 vs
   jax.core.take_current_trace swamp; 0.5.x keeps PositionalSharding AND
   resolves the modern flax/optax/haiku-free stack.
4. task.toml [environment] gpus must be 0 for hpc_controller: the candidate
   sandbox is a control layer with no local accelerator. gpus=1 made eval.py
   pass `--gpus device=0`, which fails to start on GPU-less hosts (Apple M4).

## G10.5. Discovery attempt-1: published coordinates in public/ = hidden reference leak (FIXED)

G5 attempt-1 (2026-08-19, 042-discovery-001) ran on a draft that shipped the
published initial supercells in `public/structures/` (figshare
`input_file/simulation/initial_structure/`, byte-identical). Author decision
mid-discovery: remove them — candidate constructs all five supercells from the
composition/cell spec in system.json (paper SI §1.1-1.2), same as 034's
water-molecule self-construction. The agent was already self-building
(scripts/build_systems.py: honeycomb lattice, epoxide/hydroxyl placement,
water slab) when it exhausted the 32-turn budget — it never needed the
coordinates.

Lessons for the skill:
1. **Published simulation inputs (coordinates/trajectories) are the hidden
   reference, not the candidate handout.** The paper SI documents composition
   (atoms, functional-group counts, cell) but not coordinates; a spec-level
   construction requirement is derivable and is the reproducible-paper task.
   Only ship coordinates when the paper's observable is sensitive to the exact
   initial configuration (e.g. metastable phases) — and then put them in
   hidden reference, not public.
2. **32-turn budget is too small for an end-to-end MLP Discovery run.**
   attempt-1: 46 tool calls / 809 s, agent was mid-pipeline (CP2K input
   debugged to running MD, structure builder written) with zero submission;
   attempt-2 (13/32 turns) likewise mid-construction when stopped. Author
   decision for 042-discovery-003: unlimited turn budget (--max-turns 100000,
   pagent enforces max_turns>=1; hard ceiling = case agent_timeout_sec
   86400s + container TTL 86520s) to measure capability-to-complete instead of
   budget-exhaustion. Skill default for MLP Discovery should be a long budget
   and a scoped goal (e.g. "reach a converged CP2K AIMD trajectory" per
   attempt) rather than 32 turns.
3. attempt-1 diagnosis (no SOURCE/RUNTIME blocker, agent exhausted budget) is
   a clean AGENT_LIMITATION on a sound case — the classify_failure taxonomy
   (G3) held up on first real use.

## G10. pre-existing case_factory test failures (NOT from 042)

`tests/case_factory/test_runnable_local_draft.py::test_production_smoke_validates_declared_output`
and `::test_verifier_isolation_flags_in_built_command` fail on main before any
042 change (verified by stash). Both assert VERIFIER_ISOLATION flags in the
built verifier argv; unrelated to the hpc schema/renderer work. 87 other
case_factory tests pass.
