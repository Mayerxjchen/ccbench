Train, freeze, and test a Deep Potential model for methane (CH4) using DeePMD-kit.

The training data, validation data, and a ready-to-use `input.json` are already provided at `/app/`.

Complete the following steps:

1. **Train**: Run `dp train /app/input.json`
2. **Freeze**: Run `dp freeze -o /app/graph.pb` to extract the frozen model
3. **Test**: Run `dp test -m /app/graph.pb -s /app/data/validation_data` to evaluate the model
4. **Output**: Extract the energy RMSE (in eV) from the test output and write ONLY the numeric value to `/app/out.txt`

Note: Activate the virtual environment first with `source /app/.venv/bin/activate`.
