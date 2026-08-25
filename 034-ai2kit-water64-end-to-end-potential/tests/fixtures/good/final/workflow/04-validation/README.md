# 04-validation — scientific validation of the final committee

Three agent-side validation chains against the agent's **own** data (the hidden
evaluator scores against an independent hidden set; these are the oracle's
sanity checks, mirroring the expert's post-hoc tools).

## Sub-stages

| dir | run.sh | what it does |
|---|---|---|
| `dp-test/` | `run.sh` | held-out E/F accuracy: `prepare_test_data.py` splits the mother set into the same train frames `setup.sh` samples + the rest as a DeepMD held-out set; `dp test` per committee model; `dp-test.py` prints RMSE/MAE/R2 and draws a parity plot |
| `nvt/` | `run.sh` | LAMMPS NVT 300 K with the final committee (`@DP_MODELS@` injected), `AI2KIT_NVT_STEPS` steps; `analyze_nvt.py` reports temperature/energy/pressure/model-deviation stats |
| `rdf/` | `run.sh` | O-O/O-H/H-H RDF from the AIMD mother set vs the NVT trajectory (skips first `AI2KIT_RDF_SKIP` MLP frames); saves `rdf_*_aimd.dat`/`rdf_*_mlp.dat`, `compare_rdf.png`, prints first-peak positions |

## Container adaptation vs the HPC record

- `module load` / `conda activate` replaced by `source env.sh`; the NVT heavy job
  goes through pseudo-slurm (`sbatch` + `wait_job.sh`), dp-test/RDF run inline
  (light CPU inference / analysis).
- `analyze_nvt.py`'s equilibrium cutoff (hard-coded 20000 steps in the HPC
  version) is scaled down for short smoke trajectories.
- Boxes are read from the trajectories (no hard-coded 12.42 A), same fix as the
  expert's own `compare_rdf.sh` correction.

## Markers

`dp-test.done`, `nvt.done`, `rdf.done`, `validation.done`.
