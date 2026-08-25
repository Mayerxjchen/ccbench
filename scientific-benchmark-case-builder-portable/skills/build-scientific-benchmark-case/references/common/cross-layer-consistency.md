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

For MLP cases with `public/system.json`, run:

```bash
python scripts/categories/mlp/check_draft_consistency.py CASE --json
```

Validation is read-only and fail-closed. It reports the layer and path; it does
not rewrite authored science or delete stale output.
