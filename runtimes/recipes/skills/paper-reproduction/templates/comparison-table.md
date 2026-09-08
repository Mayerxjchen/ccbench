# Comparison table — <slug>

Built from actual parsed run outputs, one row per run that produced an
observable. Scheduler/engine termination is not evidence; every value here
traces to an artifact + parse step in the run record.

| Run id | Observable | Computed value | Unit | Paper value | Evidence anchor | Tolerance | In band? |
|---|---|---|---|---|---|---|---|
| run-001 | ΔE_ads(Cu(111)) | 0.42 | eV | 0.40 | paper Fig. 3 (evidence id) | ±0.05 eV | yes/no |
| run-002 | … | … | … | … | … | … | … |

## Notes on parsing

- Derived_from: which artifact + parse script produced each computed value.
- Any unit conversion applied before comparison is stated here.
- Rows for runs that ended without a converged observable are listed in the
  discrepancy log, not here.
