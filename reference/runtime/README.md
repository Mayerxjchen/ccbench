# Runtime locks

Frozen runtime identities consumed by dispatcher/qualification tooling via
explicit CLI paths (`--cp2k-lock`, etc.). Lock files are **data, not config**:
tooling must read them; nothing regenerates them silently.

Changing a runtime identity means writing a **new** lock file (or a reviewed,
committed update) together with fresh acceptance evidence — never editing a
lock in place to match whatever happens to be deployed.

## cp2k-runtime.lock.json

- Schema: `dispatcher-cp2k-runtime-lock/v1`
- Image: `dftworld-base-ai2kit:0.1.0-cpu` (linux/amd64) → container image name `dftworld-cp2k`
- Remote SIF: `/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/cp2k-2025.2-cpu-amd64.sif`
  (`05f708b1b03d949af095a770c00ca7930fea293b161a2383d71ea99e5cfef5dd`)
- Origin: built 2026-08-24; CP2K 2025.2 / revision `c3a8adfec5`; in-container
  H2O ENERGY `-17.153015203464228 Eh` matching local docker smoke to 15 decimals
- Consumer: `scripts/qualification/run_hpc_dispatcher.sh --phase cp2k …`
  (D11 cp2k ENERGY gate; requires explicit caller authorization)

## deepmd-jax-runtime.lock.json

- Schema: `dispatcher-deepmd-jax-runtime-lock/v1`
- Image: `dftworld-base-deepmd-jax:latest` (linux/amd64), docker archive
  `ce095abebc155d3d28c9289f123a1772c47687d9127f78edf3472848b5b53b64`
- Remote SIF: `/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/deepmd-jax-0.2-cpu-amd64.sif`
  (`3634151fa6e0b3322b52c4c8e841a823701d19881e10cd265d1f3fd0bf8c6cdf`)
- Origin: built 2026-08-25 from the version-captured ai2kit-stack image;
  jax 0.5.3 / flax 0.10.6 / `deepmd_jax` editable @48a981a; G1 smoke ALL PASS
- Consumer: Case 042 draft runs / dispatcher deepmd phase
