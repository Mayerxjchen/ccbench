# positive fixture

The oracle PASS fixture is generated from a real bounded expert run
(`solution/expert/run.sh` smoke) and repackaged under `final/`. It must pass
V0-V6 in-container (deepmd-jax present).

Host-side (no deepmd-jax), the structural positive is built in-memory by
`tests/test_outputs.py::build_workspace(variant="oracle")`; the oracle
V0/V1/V3 structural pass is asserted there (`test_oracle_v0_v1_v3`).
