# Skill-Ablation v1

First trustworthy No-Skill / With-Skill comparison of the MLIP benchmark.

> **STATUS: FROZEN.** The v0 release manifest
> (`releases/ablation-ready-v0.json`) is `status: frozen` at source_commit
> `aae1bec`. Scope: cases 001-033 (031/032/033 are `benchmark_valid`; 034 is
> in construction and excluded from v0). Formal No-Skill/With-Skill
> observations may now be recorded for 031-033; the provisional
> infrastructure pilot (`skill-ablation-v1-pilot`) remains excluded from
> every formal summary.

## Frozen protocol

`protocol.yaml` predeclares everything before any score is observed:

- **Paired trials** — one `no-skill` + one `with-skill` attempt of the same
  case, on the same site and same profile. The only permitted difference is
  Skill availability (`treatment_difference: skill-availability-only`).
- **Replicates** — fixed at 3 per (case, condition).
- **Retry policy** — an `INFRA_INVALID` attempt may be replaced by a *new*
  attempt with a *new* run ID; a scientific or Agent failure may never be
  silently re-run.
- **Pilot** — `skill-ablation-v1-pilot` runs one pair per HPC case (031-034)
  to validate the machinery. Pilot observations are excluded from the formal
  result (`excluded_from_formal: true`).

## Comparability rule

Two records form a strictly comparable pair only when every frozen identity
field matches and only Skill availability differs. `tests/experiments/
test_ablation_protocol.py` enforces this: site, profile, image, benchmark
commit, verifier, platform, replicate, and agent model must be identical.

## Invalid-run ledger

`invalid-runs.json` records the run IDs of `INFRA_INVALID` observations. They
never enter a formal pair and never appear in a scientific success denominator.
Formal summaries additionally exclude any record whose experiment ID contains
`pilot`.

## Files

| Path | Role |
|------|------|
| `protocol.yaml` | predeclared experiment policy (frozen) |
| `invalid-runs.json` | INFRA_INVALID run-ID ledger |
| `releases/ablation-ready-v0.json` | frozen benchmark release manifest |
