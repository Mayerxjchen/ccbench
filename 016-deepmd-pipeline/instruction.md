Train a Deep Potential (DP) model for methane (CH4) using DeePMD-kit.

ABACUS molecular dynamics data for CH4 is provided at `/app/abacus_md/`.

Complete the full pipeline:

1. **Data conversion**: Use `dpdata` to load the ABACUS MD data from `/app/abacus_md` (format: `abacus/md`) and convert it to deepmd/npy format. Split into training data (first 161 frames) and validation data (remaining frames). Save to `/app/data/training_data` and `/app/data/validation_data`.

2. **Prepare input**: Create a training input file `input.json` with:
   - Model: `se_e2_a` descriptor, cutoff radius `rcut=6.0`, descriptor neurons `[25, 50, 100]`, `axis_neuron=16`
   - Fitting net neurons: `[240, 240, 240]`
   - Training: 2000 steps
   - Point data paths to your converted data directories

3. **Train**: Run `dp train input.json`

4. **Freeze**: Run `dp freeze -o /app/graph.pb`

5. **Test**: Run `dp test -m /app/graph.pb -s /app/data/validation_data`

6. **Output**: Extract the energy RMSE (in eV) from the test output and write ONLY the numeric value to `/app/out.txt`

Note: Activate the virtual environment first with `source /app/.venv/bin/activate`.
