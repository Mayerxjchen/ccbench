# Scientific Benchmark Builder Quality Design

## Goal

Improve `build-scientific-benchmark-case` so future scientific-agent cases
reach a trustworthy Runnable Draft quickly, expose failures through real
Discovery runs, and cannot silently drift across Prompt, public inputs,
Reference, Verifier, Solution, manifests, or generated artifacts.

The design is category-extensible. The shipped MLP category gets the first
scientific policy implementation; no rule may dispatch on case number, paper,
chemical system, or model name. Case 042 supplies regression examples only.

## Lifecycle

The Common Core lifecycle becomes:

```text
intake -> extract-spec -> design -> scaffold -> construct-runnable-draft
-> discovery -> classify -> REJECT | REFINE | PROMOTE
-> expert reference -> threshold calibration -> hidden verifier
-> evidence closure -> release freeze -> experiment handoff
```

A Runnable Draft is not a validated benchmark. It requires a stable scientific
target, traceable evidence, a coherent Candidate contract, leak-free packaging,
an executable runtime/HPC path, and durable Discovery evidence. It does not
require an expert oracle, frozen thresholds, or a formal hidden Verifier.
`benchmark_valid` remains false until deterministic release derivation closes
the post-PROMOTE gates.

## Source and scientific-identity policy

Every case design must declare one of two relationships to its source:

- `paper_faithful`: the benchmark system and method are evidence-backed
  reproductions of the cited source;
- `benchmark_adaptation`: the benchmark defines a different system or scope
  while borrowing an explicitly identified methodology.

Observed, derived, inferred, and benchmark-authored facts remain distinct.
Inferred topology, composition, method parameters, or validation targets may
not be presented as author-reported facts. Critical source conflicts prevent a
Runnable Draft until resolved or the benchmark is explicitly re-scoped as an
adaptation.

## Cross-layer contract

The case carries one machine-readable system/target contract. Deterministic
validation enforces these invariants:

1. Every publicly named/scored system has a complete public specification.
2. Prompt systems, public system records, hidden system directories/manifests,
   Verifier system rules, and public training-recipe paths have the same scope.
3. Prompt hard outcomes map to Verifier layers; the Verifier cannot require an
   unstated outcome or silently omit a stated hard outcome.
4. Reference/Solution artifacts either consume the same system contract or are
   explicitly `planned`/`deferred`; duplicated hard-coded scientific constants
   are rejected.
5. Source locks, evaluator manifests, hidden manifests, and generated-file
   locks match current bytes and counts.
6. Regeneration replaces the declared generated set and removes stale
   out-of-scope outputs before the new manifest is frozen.

The validator reports failures by layer and path. It never rewrites authored
science during validation.

## MLP full-workflow Prompt contract

For `full_data_generation_and_training`, the MLP category requires the Prompt
and design to state:

- whether the task is paper-faithful or paper-inspired;
- all target systems and every binding composition/cell/chemistry constraint;
- that every target system, or every explicitly enumerated distinct chemical
  environment, contributes first-principles labels before acceptance;
- the reference-method fingerprint and how changes start a new dataset;
- an executed train -> explore -> screen -> label -> grow -> retrain cycle;
- quantitative active-learning stopping criteria fixed before convergence is
  judged;
- held-out grouping by independent trajectory/configuration source rather than
  random adjacent-frame splitting;
- numerical energy/force metrics, stability checks, and each scored physical
  observable;
- whether published coordinates, labels, datasets, and models are permitted.
  A from-scratch task explicitly forbids using those artifacts as workflow
  inputs while allowing cited methodology evidence;
- initial-structure artifacts separately from labeled dataset artifacts;
- the machine-readable final manifest, model, data/job provenance, failures,
  trajectories, analysis code, and report.

These are policy slots, not universal numeric thresholds. Category-specific
values come from evidence and case design.

## Runnable Draft and Discovery

Add a `discovery` mode and durable failure record. Discovery runs the Candidate
on the declared real execution path and classifies the earliest material
failure as one of:

```text
SOURCE_BLOCKED
RUNTIME_BLOCKED
RESOURCE_BLOCKED
CASE_DESIGN_BLOCKED
INFRA_INVALID
AGENT_LIMITATION
```

Decision policy:

- source cannot support the target -> `REJECT`;
- runtime, resource, case-design, or infrastructure defect -> `REFINE`;
- sound case with no blocker, or an attributable Agent limitation on a sound
  case -> `PROMOTE` for expensive reference investment.

Discovery evidence records source commit, Candidate bundle digest, runtime and
site identities, run/job IDs, logs, artifacts, failure code, and classifier
rationale. A failed Discovery run is evidence, never automatically an Agent
failure.

## Reference, Solution, and preflight policy

Before PROMOTE, Reference and Solution may be absent or explicitly
`planned`/`deferred`. Existing partial assets may not claim `completed` or
invent model paths, metrics, rounds, labels, or validation evidence.

After PROMOTE:

- preflight invokes the production parser/generator/runner modules rather than
  a copied implementation;
- production and preflight share path resolution, method fingerprint, units,
  and scheduler protocol;
- scheduler work is accepted only after a terminal state; fixed sleeps are not
  completion checks;
- heterogeneous systems are evaluated per system before a declared aggregate
  is formed;
- missing models, data growth, labels, or validation artifacts fail closed;
- every expert claim is derived from existing parseable artifacts.

## Automated checks and regression fixtures

Add a Common Core cross-layer checker plus MLP-specific policy checks. Tests
must cover generic forms of the observed failures:

- public five-system scope with stale eight-system hidden outputs;
- heterogeneous atom counts incorrectly concatenated for force metrics;
- Prompt/public/Verifier system-constant drift;
- public recipe paths naming systems without public specifications;
- hidden regeneration that leaves obsolete directories;
- stale source/evaluator hashes and stale frame/system counts;
- preflight logic that duplicates rather than imports production code;
- scheduler submission followed by conversion before terminal state;
- missing model or lineage reported as completed;
- paper-derived and benchmark-authored facts conflated.

Tests assert semantic invariants and parsed artifacts, not exact prose.

## Packaging and compatibility

Preserve the portable package layout and explicit invocation policy. Update
the skill entrypoint, routed references, scripts, templates, test fixtures,
manifest, and `SHA256SUMS` together. Existing modes remain accepted; the new
funnel adds `discovery` and explicit Runnable Draft semantics without granting
automatic compute authorization.

After the portable package passes its full test/install contract, uninstall
only the Claude Code copy at:

```text
/Users/chenxuanjie/.claude/skills/build-scientific-benchmark-case
```

The repository portable source and all Codex skills remain untouched by the
uninstall operation.

## Acceptance criteria

- Existing portable tests remain green.
- New regression tests fail on the old skill/package and pass after changes.
- A generic MLP fixture demonstrates all cross-layer invariants without using
  Case 042 identifiers or chemistry-specific constants.
- `install.sh --check`, package manifest verification, and `SHA256SUMS`
  verification pass.
- The Claude Code installed copy is absent after uninstall; the portable source
  remains present and clean.
