# Failure diagnosis and attempt recovery

## Read the states honestly

- `PENDING` a long time: queued externally. Keep polling; this costs you
  nothing. Only escalate to the operator if capabilities-adjacent limits
  (walltime ceilings) make the request impossible.
- `FAILED`: read `logs` first. Classify before touching anything.
- `TIMEOUT`: the walltime killed it. Either the estimate was wrong or the
  physics is not converging; check engine checkpoints.
- `CANCELLED`: someone/something cancelled it. Do not treat as scientific
  evidence.

## Classification → action

| Symptom | Diagnosis | New attempt? |
|---|---|---|
| OOM / memory kill | resources too small | yes: raise memory, same command |
| walltime TIMEOUT | estimate short or slow convergence | yes: raise walltime or fix convergence; checkpoint if supported |
| input digest mismatch at staging | file edited after digest computed | fix locally, recompute digest, submit attempt+1 |
| engine error in logs (bad input card, missing file) | science-side defect | fix input; attempt+1 |
| node/hardware noise (ECC, I/O errors) | infrastructure | ask the operator; do not burn attempts guessing |
| parser says outputs are garbage despite SUCCEEDED | scientific failure | diagnose engine-side; attempt+1 with the fix |

## Never

- Never skip attempt numbers or reuse a spent attempt — the gateway rejects
  it, and if it did not, the lineage would be a lie.
- Never run two attempts of one operation concurrently.
- Never treat a transport duplicate (`duplicate: true`) as a new attempt.
- Never fetch undeclared paths or try to repair a submission from remote
  diagnostics — evidence outside an explicit fetch is not submission input.
