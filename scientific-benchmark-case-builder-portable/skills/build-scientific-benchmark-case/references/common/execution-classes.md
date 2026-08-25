# Execution Classes

Both classes share the same trust, evidence, and release contracts and the
same hidden-evaluator lifecycle. They differ only in compute path.

## local_sandbox

Isolated local compute inside the Candidate sandbox.

- No site fields: no hostname, partition, account, scheduler, or SSH fields in
  any profile.
- No HPC overlay assets (`profiles/resource.yaml`, `profiles/platform.yaml`,
  `reference/compute-runtime.lock.json`).
- Approved steps run as local processes under the sandbox; an unrestricted
  evaluator-host process is development-only and cannot generate official
  adversarial evidence.

## hpc_controller

The Agent is the control layer on HPC; it issues batch submissions and fetches
results.

- Requires `profiles/resource.yaml` (batch/GPU requirements) and
  `profiles/platform.yaml`.
- `reference/compute-runtime.lock.json` records the verified runtime identity.
- A faithful scheduler stand-in is acceptable for smoke evidence; a simulated
  scheduler is not acceptable as formal evidence.
- HPC infrastructure failure is not a scientific failure; it is recorded as
  infrastructure invalidity, never as an Agent correctness verdict.

## Shared contract

- Same positive-allowlist Candidate bundle and audit (see
  `public-hidden-boundary.md`).
- Same gate closure, evidence sealing, and release derivation
  (`check_release.py`).
- The Candidate is destroyed before a fresh hidden Verifier runs, on both
  classes.
