# Runtime locks

Frozen runtime identities consumed by dispatcher/qualification tooling via
explicit CLI paths (`--cp2k-lock`, `--ai2kit-lock`). Lock files are **data,
not config**: tooling must read them; nothing regenerates them silently.

Changing a runtime identity means writing a **new** lock file (or a reviewed,
committed update) together with fresh acceptance evidence — never editing a
lock in place to match whatever happens to be deployed. Where a lock carries
an empty `runtime.sif_sha256`, that digest is mandatory runtime data to be
captured at the site, not invented; an empty digest keeps its qualification
gate NOT_RUN.

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
- Consumer: Case 005 (legacy 042) draft runs / dispatcher deepmd phase

## ai2kit-runtime.lock.json

- Schema: `dispatcher-ai2kit-runtime-lock/v2` — identity `ai2kit-runtime-v1`,
  capability `ai2kit`, mode `derived-runtime-reuse`
- **Runtime, not a control plane** (Architecture Freeze §4): holds no SSH
  credentials, issues no raw sbatch, shared rather than per-case
- Lineage (R2, 2026-09-03): the original `dftworld-base-ai2kit` controller
  image is unrecoverable (archive deleted after cp2k acceptance, recipe never
  committed). This runtime's rootfs **is** the trusted CP2K SIF rootfs
  (parent `05f708b1…5cfef5dd`), which offline inspection proved already ships
  `ai2_kit` 1.1.0 with `/opt/ai2kit/bin` materialized first in PATH; direct
  reuse of the locked SIF was approved instead of building a new image. The
  lock records apptainer 1.4.0, the rootfs manifest digest
  (`5c7e6edf…3572`, sorted `unsquashfs -l` listing), and keeps the
  unrecoverable OCI/Dockerfile/archive digests **null** — never fabricated.
- Software: `ai2_kit` 1.1.0 (= Case 004 lock + registry `ai2kit-runtime-v1`); the
  package has no `__version__` (empty `__init__.py`) — probe via
  `importlib.metadata.version("ai2_kit")`
- Remote SIF: same file as the cp2k lock
  (`05f708b1b03d949af095a770c00ca7930fea293b161a2383d71ea99e5cfef5dd`)
- Consumer: `scripts/qualification/run_hpc_dispatcher.sh --phase ai2kit …`
  + `--ai2kit-lock runtimes/locks/ai2kit-runtime.lock.json`
  (Case 004 `runtime.ai2kit` qualification gate; requires explicit caller authorization)
- If an R4 replayable build replaces this runtime, its digest changes and
  site qualification MUST be re-run under a new candidate tag

## matclaw-cips-runtime.lock.json

- Schema: `dispatcher-compshare-runtime-lock/v2`
- Capability: `matclaw-cips`
- Image: `mlff-matclaw-cips-gpu-v1` (`compshareImage-1uw6sd44931i`)
- Target cases: Cases 001, 002, 003
- Recipe: `runtimes/recipes/matclaw-cips-gpu/recipe.lock.json`
- Qualification status: `BUILT_NOT_QUALIFIED` (in public repository; production verification relies on site qualification receipt)

## jax-runtime.lock.json

- Schema: `dispatcher-compshare-runtime-lock/v2`
- Capability: `jax`
- Image: `mlff-jax-gpu-v1` (`compshareImage-1uyaneriamfz`)
- Target case: Case 005
- Recipe: `runtimes/recipes/jax-gpu/recipe.lock.json`
- Public GitHub status: `BUILT_NOT_QUALIFIED / external receipt required`
  - Build provenance is verified: image ID, recipe digest, and source archive SHA are locked.
  - Formal qualification receipt requires cryptographic verification with maintainer site private key and is mounted at runtime; the public repository does not bundle private signing keys.

