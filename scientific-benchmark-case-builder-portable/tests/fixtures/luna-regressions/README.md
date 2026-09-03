# Luna v1 regression fixtures

`mvp-passing/` is a complete, self-contained MLP case tree that clears every
check of `scripts/common/check_discovery_runnable.py` (shipped frozen at
`case_status: draft` — the gate derivation is re-run by the tests, never
pre-baked). `tests/test_luna_v1_regressions.py` starts from copies of this
tree and injects exactly one drift each to reproduce the four documented
Luna v1 forward-test failures (spec `unknown`+concrete value,
instruction/bundle `public/` path drift, prose-only fixture matrix,
builder-self-test masquerading as `tests/test.sh`) plus the cross-layer and
single-writer guards. The frozen original artifact under
`experiments/case-builder-luna-v1/` is also asserted non-runnable when
present.
