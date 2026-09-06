# CIPS domain-wall search

Use the provided CIPS monolayer and DeePMD potential to perform an adaptive
search for electric-field and temperature conditions that yield sequential
domain-wall propagation. Start at `Ez=-0.01 V/angstrom, T=200 K`; remain within
`Ez in [0,-0.3] V/angstrom` and `T in [0,250] K`; run no more than two MD jobs in
one search iteration. Each next point must be justified by preceding measured
results.

Apply the documented effective-charge field protocol. Quantify propagation by
fitting mean absolute flip-time separation against site distance for distances
1 through 10. Deliver `/app/result.json`, `/app/search_history.csv`, raw runs,
the best trajectory, `/app/domino_analysis.csv`, and a figure. A successful
trajectory has a fitted slope above 0.3 ps/site and visible sequential
propagation. Do not use network access or commercial software.

## Execution

A remote HPC capability exists in the sandbox. Discover and use the available
infrastructure yourself. Long-running scientific work (DeePMD training and MD
sampling) must use the remote scheduler; the sandbox is the control layer.
Fetch every run's artifacts back under `/app` so the deliverables above are
complete in the graded workspace.
