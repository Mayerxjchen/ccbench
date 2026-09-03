# Toy source ecosystem — LC-MLP v1 (v3 regression props)

A deliberately small, synthetic paper ecosystem used as the held-out source
for the v3 clean-context short regression
(`../PROTOCOL.md`). Task framing: build a Discovery-MVP benchmark case that
asks an Agent to **evaluate the published LC-MLP v1 checkpoint** against the
re-serialized descriptor table and reproduce the reported metric band. No
retraining is implied.

Contents:

- `paper/paper.md` — toy methods + reported metrics (MAE 0.031 / RMSE 0.045).
- `repo/` — config, published checkpoint, README.
- `data/dataset.csv` + `data/README.md` — 24-cluster calibration table.
- `LICENSE-NOTES.md` — per-artifact license/access states.

Everything here is fictional; it exists only to exercise the skill end to
end. The files present are the complete universe the builder may inspect.
