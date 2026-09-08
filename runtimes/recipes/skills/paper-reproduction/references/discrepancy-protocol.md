# Discrepancy protocol

When a run's observable lands outside the acceptance band (or inside it by
suspicious luck), you do **not** immediately rerun. You diagnose, in a fixed
order, and the diagnosis decides the *next run reason* and which inputs or
steps change. Diagnosis is recorded in `templates/discrepancy-log.md` and every
step is either performed or explicitly skipped with a reason.

## Fixed diagnosis order

```text
extraction
→ convergence
→ provenance
→ method
→ structure
→ sampling
→ implementation
→ underspecification
→ contradiction
```

| Step | Question it answers | Typical action if confirmed |
|---|---|---|
| `extraction` | Did I restate the paper's claim/observable/conditions correctly? | correct the extraction, note it, re-check (a changed target is a new contract version only if scope changes) |
| `convergence` | Was the computed quantity converged (k-grid, cutoff, timestep, ensemble length, relaxation tolerance)? | lengthen/refine; next run reason `faithful_fix` |
| `provenance` | Is the evidence/input I used really the one the paper used (file, hash, version)? | re-anchor to the correct artifact; `faithful_fix` |
| `method` | Did I use the same method/functional/potential/algorithm as the paper? | switch method only under the contract's method fingerprint; if the paper is silent this becomes `underspecification` |
| `structure` | Is the system/geometry/configurational ensemble equivalent? | fix structure generation; `faithful_fix` |
| `sampling` | Is the statistical sampling representative (thermostat, seeds, independent samples, equilibration)? | more/independent samples; `faithful_fix` or `sensitivity_test` |
| `implementation` | Is the code path itself defective (parse bug, unit error, wrong axis)? | fix the implementation; `faithful_fix` |
| `underspecification` | Did the paper simply not supply a parameter that matters? | make and record a choice, then `gap_closure` or `sensitivity_test` |
| `contradiction` | After faithful execution and honest gap closure, the claim still does not hold | record a `contradicted` verdict path; this is a finding, not an error |

## Rules

- **Order is mandatory.** Skip a step only with a one-line recorded reason
  (e.g. "`convergence` skipped — same k-grid and cutoff as paper SI, verified
  converged to < tol"). Jumping to `implementation` or `contradiction` without
  the earlier steps is how wrong "the paper is wrong" conclusions get made.
- **One confirmed cause per diagnosis round** unless the log shows otherwise.
  Changing many variables between two runs makes the comparison meaningless.
- **Termination ≠ evidence.** A scheduler `COMPLETED` or exit code `0` does not
  put a run inside the acceptance band. Compare parsed physical quantities.
- **No blind retry.** A second run without a recorded diagnosis and a declared
  run reason is not a valid run; the validator requires each run record to
  state its reason.
- **Never tune to target.** A discrepancy is resolved by finding a faithful or
  honestly-gap-filled cause, never by choosing inputs or thresholds to land on
  the reported number.

## Output

For every diagnosis round write one `discrepancy-log.md` entry: the run
recorded, the steps checked (in order, pass/fail/skip+reason), the confirmed
cause, and the resulting next run reason and changed inputs. The verdict step
later reads these logs as the evidence chain for `reproduced`,
`contradicted`, or the intermediate verdicts.
