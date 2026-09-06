# negative fixtures

One per hard outcome. Structural negatives (no model, corrupt model, forged
labels, no AIMD provenance, nonphysical energies, no active-learning loop,
duplicate/static dataset, unused new labels) are built in-memory by
`tests/test_outputs.py::build_workspace(variant=...)` and asserted to fail the
targeted V0/V1/V2/V3 layer.

Scientific negatives (bad hidden E/F, NVT crash, shifted density profile) are
formal-phase: they require a real trained model and deepmd-jax in the container;
the threshold logic is exercised deterministically on the host via monkeypatched
`run_hidden_ef` / `run_hidden_nvt` / `compute_density_profile` unit tests.
