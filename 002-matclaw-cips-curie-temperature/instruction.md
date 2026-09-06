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

A remote HPC capability exists in the sandbox. Discover and use the available
infrastructure yourself. Long-running scientific work (DeePMD training and MD
sampling) must use the remote scheduler; the sandbox is the control layer.
Fetch every run's artifacts back under `/app` so the deliverables above are
complete in the graded workspace.
