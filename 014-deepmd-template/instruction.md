Train a Deep Potential (DP) model for methane (CH4) using DeePMD-kit.

Training data and validation data are provided at `/app/data/training_data` and `/app/data/validation_data`.

A template input file is provided at `/app/input_template.json` with some placeholders marked as `FILL_ME`. Fill in the missing parts to create a valid `input.json`, then train and evaluate the model.

The missing parts are:
1. `FILL_ME_TYPE_MAP` — the element type map. The data contains H and C atoms (list them in alphabetical order: `["H", "C"]`)
2. `FILL_ME_RCUT` — the cutoff radius for the descriptor (use `6.00`)
3. `FILL_ME_NEURON` — the descriptor neuron sizes (use `[25, 50, 100]`)
4. `FILL_ME_STEPS` — the number of training steps (use `2000`)

After filling the template:
1. Train: `dp train /app/input.json`
2. Freeze: `dp freeze -o /app/graph.pb`
3. Test: `dp test -m /app/graph.pb -s /app/data/validation_data`
4. Extract the energy RMSE (in eV) from the test output and write ONLY the numeric value to `/app/out.txt`

Note: Activate the virtual environment first with `source /app/.venv/bin/activate`.
