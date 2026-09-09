# CIPS active distillation

Using the provided CIPS structure, DeePMD teacher potential, and source paper,
construct and execute an active-distillation workflow for a fast student
DeePMD potential. Generate initial teacher-labelled molecular-dynamics data at
multiple temperatures, keep independent train and held-out test sets, train a
student committee, explore configuration space with student MD, select
informative configurations from committee force disagreement, relabel those
configurations with the teacher, and retrain.

Stop when the held-out force MAE is below 0.10 eV/angstrom or after five active
iterations. Deliver `/app/result.json`, trained models, raw train/test data, MD
trajectories, and `/app/active_learning/history.json`. Every reported metric
must be reproducible from the delivered artifacts. Do not use network access or
commercial software.

## Execution

Long-running scientific work may require external CPU/GPU compute. Keep the
Candidate workspace self-contained, and write a request draft under
`compute-requests/` only when local preflight is insufficient. The trusted
host validates the draft and an external Operator returns declared artifacts.
Fetch every run's artifacts back under `/app` so the deliverables above are
complete in the graded workspace.
