# Cross-layer Consistency

Load this during design, construction, regeneration, validation, and before a
Runnable Draft is frozen.

## One authority per fact

Binding system/target identity lives in one machine-readable public contract.
Prompt prose, hidden manifests, Verifier plans, public recipes, Reference, and
Solution consume that authority; they do not maintain independent constants.

Required invariants:

1. Every scored system has a complete public specification.
2. Public systems, hidden directories/manifests, Verifier scope, and explicit
   public recipe paths name the same set.
3. Prompt hard outcomes map to Verifier layers in both directions.
4. Source locks, evaluator manifests, hidden manifests, generated-file locks,
   frame counts, and system counts match current bytes.
5. Regeneration replaces the declared generated set and removes stale
   out-of-scope directories before freezing its manifest.
6. Reference/Solution consume the public contract or declare a precise
   `planned`/`deferred` state.

MVP machine invariants (enforced by the checker, fields declared in
`case-design.yaml` / `task.toml` / `verifier-plan.yaml` /
`public/input-manifest.json` — never parsed from prose):

7. One submission root across `task.toml [candidate].submission_root`,
   `case-design submission.root`, and `verifier-plan submission_root`.
8. Input paths referenced by the instruction exist in the packaged bundle;
   `public/input-manifest.json` `candidate_path`s are staged by the candidate
   file rules; the submission schema reaches the Candidate.
9. Every `validation_sets` entry has exactly one owner
   (`candidate_generated` | `verifier_hidden` | `expert_calibration`).
10. `metric_contract` operators match the comparators used in the instruction
    for the same threshold value.
11. `dft_dynamics` is claimed only when `label_source: dft`; teacher or
    published-model labeling is not DFT.
12. `CONTRACT.md` is staged into the bundle if and only if
    `contract.candidate_visible: true`.
13. When `case-design source_context` is declared, the leave-one-out intake
    contract holds: the `allow` roots resolve, every entry in
    `source/sources.lock.json` sits under an allow root and under a
    declaration whose kind matches it, and no answer file is consumed — names
    matching the design's `exclude` or the default blocked list
    (`acceptance.json`, `expected-output.json`, `expected.json`,
    `held-out-targets.json`, `scores.json`) may never be locked whole, and a
    locked directory tolerates such files only when `hash_sources.py
    --exclude` recorded the omission in the lock.
14. `verifier-plan.yaml` has exactly one authority: `derive_verifier_plan.py`
    over `case_kind` + `workflow_capabilities`. Every applicable layer appears
    explicitly with `status: selected` or `status: deferred` (a mandatory
    layer's deferral carries a non-empty reason from the design's
    `verifier_deferrals`); applicable layers are never silently absent.

For MLP cases with `public/system.json`, run:

```bash
python scripts/categories/mlp/check_draft_consistency.py CASE --json
```

Validation is read-only and fail-closed. It reports the layer and path; it does
not rewrite authored science or delete stale output.
