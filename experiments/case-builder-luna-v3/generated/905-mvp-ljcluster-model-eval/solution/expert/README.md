# Expert Solution

> Hidden from Candidate. One valid reference implementation; an alternative
> valid implementation must also be accepted by the verifier.

Follows `references/common/reference-and-solution-policy.md`.

- Status: **planned / deferred at the Discovery MVP** — no executed
  reference exists yet; `reference/reference.json` stays `planned`, implicit
  entry only.
- inputs (frozen and hashed into `evaluator-manifest.json` at release):
  the published checkpoint `public/lc-mlp-v1/weights/lc-mlp-v1.txt`, the
  configuration, and the descriptor table — to be pinned at release.
- runtime identity: to be recorded (local sandbox) when the reference is
  executed.
- commands: to be recorded when the reference is executed.
- independent parser (separate trust domain from this expert): TBD.
- lineage entry recorded in `reference/reference.json`: to be added by the
  reference run at release.