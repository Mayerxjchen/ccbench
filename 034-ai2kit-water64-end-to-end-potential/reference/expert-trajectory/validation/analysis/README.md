# analysis/ — expert post-hoc validation tools

These are byte copies of the expert's **post-hoc validation scripts** from
`ai2kit/test/` on the real HPC. They are the tools the expert used to check the
final iter-005 models **after** the 5-round active-learning loop finished.

They are divided into three validation chains:

## 1. Held-out dp-test (`dp-test.py`, `prepare_test_data.py`, `run_dp_test.sh`)

- `prepare_test_data.py` splits `config/aimd.xyz` (190 frames) into the same
  50-frame initial-training subset `workflow/setup.sh` would sample, and writes
  the remaining **140 frames** as a held-out DeepMD system dir
  (`test/dp-test/test-data/O64H128/`).
- `run_dp_test.sh` (Slurm) runs `dp test` for each of the 4 final models and
  plots a parity figure.
- `dp-test.py` computes RMSE / MAE / R2 for energy and forces and draws the
  parity + error-distribution plot.

Reference numbers achieved: Energy RMSE 6.710e-4 eV/atom, Energy MAE 5.560e-4,
Energy R2 0.954; Force RMSE 4.288e-2 eV/Å, Force MAE 3.331e-2, Force R2 0.997.

> **Caveat for 034:** this 140-frame held-out set is derived from the *public*
> 190-frame mother set, so an agent that trains on all 190 public frames would
> "pass" it trivially. It is NOT a safe hidden test for 034. 034 therefore adds an
> **independent hidden CP2K validation set** (see `../../hidden-validation/`).

## 2. 300 K NVT stability (`run_nvt.sh`, `analyze_nvt.py`)

- `run_nvt.sh` (Slurm) runs a LAMMPS NVT at 300 K with the 4-model committee
  (phase 1: 10 ps equilibration, 20,000 steps; phase 2: extend to 90 ps by
  restart), using `config/nvt/nvt.in` with the model paths injected via `sed`.
- `analyze_nvt.py` reads `thermo.dat` + `model_devi.out` and reports temperature
  statistics (mean/std/fluctuation), energy fluctuation, pressure, max force
  deviation, and trajectory frame count, with a pass/fail judgment.

## 3. RDF comparison (`compare_rdf.sh`, `plot_rdf.py`)

- `compare_rdf.sh` computes O-O / O-H / H-H RDFs from the filtered AIMD mother
  set (`config/aimd.xyz`, 190 frames) and the MLP NVT trajectory
  (`test/nvt/output/dump.lammpstrj`, skipping the first `MLP_SKIP_FRAMES`=100
  frames of equilibration), then plots AIMD vs MLP.
- `plot_rdf.py` is an older/standalone RDF plotting script (kept for reference;
  it points at hard-coded raw-output paths — the authoritative workflow uses
  `compare_rdf.sh`).

Reference RDF first peaks (AIMD / MLP): O-O 280/278 pm, O-H 98/98 pm,
H-H 158/158 pm.

## Note on paths

The byte copies retain the original HPC absolute paths (e.g.
`/public/home/<site-user>/ai2kit/ai2kit/...`) and HPC module/conda activation lines.
They document the expert workflow as it actually ran; the containerized
`solution/expert/` and the `tests/` verifier are the adapted forms.
