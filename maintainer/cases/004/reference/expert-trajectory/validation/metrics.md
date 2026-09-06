# Validation metrics — expert oracle (hidden ground truth)

Source: expert record `ai2kit/plans.md`, post-hoc validation of the final
`iter-005` DeePMD model. These numbers anchor `reference/thresholds.json`
levels L7/L8/L9 (they are *drafts* until frozen after container reruns).

## dp-test on the 140-frame held-out subset of the mother set

| Metric | Value |
|--------|-------|
| Energy RMSE | 6.710e-4 eV/atom |
| Energy MAE  | 5.560e-4 eV/atom |
| Energy R²   | 0.954 |
| Force RMSE  | 4.288e-2 eV/Å |
| Force MAE   | 3.331e-2 eV/Å |
| Force R²    | 0.997 |

Method: `validation/analysis/prepare_test_data.py` splits the 190-frame mother
set into 50 training + 140 held-out (same random seed as `workflow/setup.sh`);
`run_dp_test.sh` runs `dp test` on the held-out set against the iter-005 model.

## 300 K NVT (production LAMMPS run)

| Item | Value |
|------|-------|
| Thermostat | NVT 300 K, `config/nvt/nvt.in` |
| Observed temperature band | 251.6–362.5 K, mean 299.8 K |
| Lost atoms / NaN | none |

## RDF first peaks (AIMD vs MLP)

| Pair | AIMD / Å | MLP / Å |
|------|----------|---------|
| O–O | 2.80 | 2.78 |
| O–H | 0.98 | 0.98 |
| H–H | 1.58 | 1.58 |

Method: `analysis/compare_rdf.sh` — reference RDF from the 190-frame
`aimd/processed/aimd.xyz`, MLP RDF from the verifier/production NVT trajectory
via `analysis/plot_rdf.py`.

## Trajectory-fidelity table (hidden E/F design)

`dp test` above is the *expert-internal* number. The hidden verifier (L7) scores
a submitted model against the **full 190-frame `aimd.xyz`** (`dft-validation.extxyz`),
which is a slightly larger, temperature-windowed set — hence draft L7 caps are
looser than the held-out numbers above (~1.5×). See `thresholds.json`.
