Train a DeePMD model for methane using the provided datasets.

Training and validation data are at `/app/data/training_data` and `/app/data/validation_data`. A template is at `/app/input_template.json` with `FILL_ME_RCUT`.

Read `/app/data_geometry.txt`, which reports the **maximum interatomic distance** observed in the training set (Angstrom).

Choose **`rcut`** as follows:
- Let `d_max` be that maximum distance.
- `rcut` must be one of **4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0** (Angstrom).
- Pick the **smallest** value in that list with **`rcut >= d_max + 2.0`**.

Write your choice as `RCUT=<value>` to `/app/params.txt` (example: `RCUT=6.0`). Fill the template to create `/app/input.json` (keep other hyperparameters as in the template), then:

1. `source /app/.venv/bin/activate`
2. `dp train /app/input.json`
3. `dp freeze -o /app/graph.pb`
4. `dp test -m /app/graph.pb -s /app/data/validation_data`
5. Write **only** the energy RMSE (eV) from the test log to `/app/out.txt`
