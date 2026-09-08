# Discrepancy log — <slug>

One entry per diagnosis round. Steps are checked in the fixed order; each step
is pass / fail / skipped+reason. A confirmed cause determines the *next run
reason* and what changes.

Diagnosis order: extraction → convergence → provenance → method → structure →
sampling → implementation → underspecification → contradiction

## Entry — after <run-00N>

**Observed:** <observable value vs acceptance band>

| Step | Result | Note |
|---|---|---|
| extraction | | did I restate claim/observable/conditions correctly? |
| convergence | | k-grid / cutoff / timestep / ensemble length / relaxation converged? |
| provenance | | evidence/input really the one the paper used (hash)? |
| method | | same method/functional/potential/algorithm? |
| structure | | equivalent system/geometry/configurational ensemble? |
| sampling | | representative statistics / seeds / equilibration? |
| implementation | | parse bug, unit error, wrong axis, code defect? |
| underspecification | | did the paper omit a parameter that matters? |
| contradiction | | faithful execution + honest gap closure still fails? |

**Confirmed cause:**
**Next run reason:** faithful_fix / gap_closure / sensitivity_test
**Changed inputs/steps:** <what run-00(N+1) will do differently>
