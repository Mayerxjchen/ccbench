# CIPS Curie temperature

Use the provided CIPS structure and DeePMD potential to estimate the Curie
temperature from molecular dynamics. Before the full temperature sweep, run a
pilot near the transition and demonstrate that the chosen ferroelectric order
parameter and production length are converged. The production sweep must bracket
the transition and use longer sampling where the pilot indicates it is needed.

Deliver `/app/result.json`, `/app/order_parameter.csv`,
`/app/curie_temperature.png`, `/app/report.md`, and all raw trajectories under
`/app/md/`. The result must state the supercell, order-parameter definition,
transition estimate, uncertainty, sampling lengths, and convergence evidence.
Do not use network access or commercial software.

## Execution

Long-running scientific work may require external CPU/GPU compute. Keep the
Candidate workspace self-contained, and write a request draft under
`compute-requests/` only when local preflight is insufficient. The trusted
host validates the draft and an external Operator returns declared artifacts.
Fetch every run's artifacts back under `/app` so the deliverables above are
complete in the graded workspace.
