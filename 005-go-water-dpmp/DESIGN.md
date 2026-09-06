# Case 042 Design — GO–water DPMP end-to-end

## Scope (user-approved)
- case_kind: `end_to_end_model_development` -> reproduction_scope `full_data_generation_and_training`
- **Agent must regenerate labels**: candidate constructs structures, runs CP2K
  (revPBE-D3) AIMD labeling, trains DPMP (deepmd_jax), iteratively improves via
  active learning, validates. The published 14140-frame dataset and author
  model.pkl are HIDDEN reference assets, not candidate inputs.
- Feasibility note: GO cells are 489-816 atoms; a benchmark agent must bound its
  labeling volume. Agent budget 24h; the case does not require matching the
  paper's full 14140-frame volume, only a faithful executed pipeline whose
  trained model passes hidden accuracy/stability/property thresholds.

## Capabilities (all required)
structure_generation, dft_dynamics, model_training, iterative_improvement,
hidden_static_accuracy, hidden_dynamic_stability, hidden_physical_observable.

## Execution class
`hpc_controller` (034 pattern). Agent is control layer: sbatch -> batch jobs
(CP2K AIMD on CPU nodes, DPMP training on GPU nodes), artifact_fetch. Faithful
pseudo-slurm stand-in for smoke; real cluster for formal. HPC failure = infra
invalidity, never agent verdict.

## Runtime
New base image `dftworld-base-deepmd-jax` (user-approved). Construct builds it
extending `dftworld-base-ai2kit` (brings CP2K + ai2-kit workflow) with
`deepmd-jax` + `jax[cuda]` for DPMP training/eval. Candidate container also
needs `jax-md` (exploration MD, paper stage two). GPU build + site push require
authorization.

## Observables (hidden)
| Layer | Outcome | Hidden asset |
|---|---|---|
| V4 static | energy/force RMSE vs DFT labels | held-out frame subset of published 14140 (seed-frozen, overlap-checked against candidate submission) |
| V5 dynamic | short NVT (300 K, ~10 ps) stability, no collapse/unphysical spikes | trajectory checks, reference envelope |
| V6 physical | water density profile across GO–water interface (z) | AIMD-derived reference profile; compare peak positions / depletion width |

## Public / hidden split
- public/: instruction.md, task.toml, SI-derived system definitions (cell size,
  composition, oxidation level per interface), initial structures
  (published, CC BY 4.0), reference-method fingerprint (revPBE-D3/CP2K),
  deepmd_jax trainer config from the paper.
- hidden/: 14140-frame dataset, model.pkl, held-out subset, thresholds,
  density-profile reference, all fixtures, profiles, evidence.

## Gate plan
G0 source/category/scope freeze — extract-spec ready, design freeze
G1 public/hidden boundary — copy-then-audit positive allowlist
G2 runtime/execution contract — build dftworld-base-deepmd-jax, smoke
G3 instruction/output-contract fidelity — instruction states all hard outcomes abstractly
G4 expert reference reproducibility — reference runs bounded end-to-end pipeline
G5 hidden validation independence — hidden set from published data, submission-overlap check
G6 outcome-based verifier coverage — V0-V6 each map to outcome + evidence
G7 positive/negative/alternative-valid fixtures
G8 threshold calibration — n_runs>=2 independent reference runs, frozen pre-inspection (stochastic: true)
G9 evidence/provenance integrity — sealed, restorable
G10 end-to-end reward chain — verifier gates wired to reward
G11 independent rerun — reproduce reference metrics
G12 final release freeze — check_release.py only writer of benchmark_valid

## Authorized approvals pending
- build dftworld-base-deepmd-jax (GPU)
- reference end-to-end pipeline run (HPC, bounded)
- threshold calibration runs (n_runs>=2)
