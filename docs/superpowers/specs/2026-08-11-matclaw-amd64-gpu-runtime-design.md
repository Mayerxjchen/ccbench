# MatClaw amd64 GPU Runtime Completion Design

## Purpose

Close the runtime gap that currently prevents Cases 031–033 from running on the
x86_64 A100 cluster. The existing locked SIF was derived from an Apple Silicon
image and is `arm64`; it cannot execute on the cluster. A CPU-only amd64 SIF is
useful for transport and diagnostic checks but is not eligible for formal GPU
paper runs.

This design is a P0 prerequisite for the diagnostic and formal-run tasks in the
existing MatClaw plans. It changes infrastructure qualification only. SSH,
Slurm, Apptainer, and GPU mechanics remain transparent infrastructure and are
not scored as evaluated-Agent capabilities.

## Runtime Artifacts

The build produces four distinct immutable artifacts:

1. an amd64 CPU OCI image used as the cross-architecture base;
2. an amd64 GPU OCI image containing the pinned CUDA-capable
   TensorFlow/DeePMD runtime;
3. the Docker archive used to transfer that exact GPU image to the cluster;
4. an amd64 GPU Apptainer SIF built from that archive.

Every artifact has a separate name and SHA-256 identity. CPU and GPU tags must
never alias one another. The formal SIF filename contains both `gpu` and
`amd64`.

## Build Path

The supported Apple-Silicon-to-cluster path is:

```text
docker buildx --platform linux/amd64 (CPU base)
  -> docker buildx --platform linux/amd64 (GPU image)
  -> docker save (GPU archive)
  -> checksum + rsync
  -> apptainer build from docker-archive
  -> checksum + architecture inspection
  -> Slurm A100 qualification with apptainer --nv
```

The cluster is not required to access Docker Hub. No formal artifact is built
from a floating tag or from an unrecorded local image.

## Lock Migration

The current `031-runtime-lock.json` remains auditable and is explicitly marked
ineligible with `invalid_reason=architecture_mismatch`. It is not silently
rewritten into evidence that the old build succeeded.

A shared GPU runtime lock is generated for 031–033. It records:

- schema version and `formal_eligible`;
- `os=linux`, `architecture=amd64`, and expected compute architecture
  `x86_64`;
- CPU-base image ID and GPU image ID;
- OCI manifest/repository digest when available, without mislabelling a Docker
  image ID as a repository digest;
- Docker archive SHA-256, SIF path, and SIF SHA-256;
- Dockerfile, qualification script, source commit, and resolved package-set
  hashes;
- Apptainer version, build method, timestamps, and builder architecture;
- qualification receipt path and SHA-256.

All three case evidence manifests reference the shared lock by content hash.

## Qualification Gates

A runtime becomes formal-eligible only when one fixed qualification job on the
target A100 satisfies all of the following:

1. transferred Docker archive hash matches the lock candidate;
2. SIF hash matches the lock candidate;
3. SIF inspection succeeds and reports Linux amd64/x86_64 compatibility;
4. `apptainer exec` reports `uname -m=x86_64`;
5. `apptainer exec --nv` exposes the allocated A100;
6. TensorFlow and DeePMD enumerate a GPU and do not silently fall back to CPU;
7. locked structure/model evaluation is finite;
8. CPU/GPU energy and maximum force-component differences are each `<1e-6`;
9. the 100-step MD trajectory has 101 finite frames;
10. software versions and measured seconds per step are recorded.

A CPU SIF, arm64 SIF, missing receipt, stale receipt, failed parity result, or
receipt for a different SIF hash forces `formal_eligible=false`.

## Controller and Slurm Contract

The controller retains exactly the six public operations
`stage/submit/status/log/cancel/fetch`. No arbitrary shell, SSH option, or new
remote-execution API is added.

The existing fixed `probe` run kind generates a qualification receipt. A
`paper` or `smoke` submission requires a lock and a fetched receipt whose SIF
hash, cluster identity, GPU identity, and qualification policy match. The
controller rejects CPU, arm64, unqualified, or stale runtime locks before
submitting scientific work.

The fixed Slurm entry point repeats fail-closed checks inside the allocated
job: SIF hash, `uname -m`, A100 visibility, TensorFlow GPU visibility, and
required environment/output binds. This protects against a remote file being
replaced after controller validation.

The controller is generalized from a Case-031-only path policy to an explicit
allow-list for Cases 031, 032, and 033. Each case has a separate confined remote
root. Case IDs, roots, Slurm resources, and allowed run kinds remain fixed
policy rather than caller-supplied shell data.

## Failure Semantics

Architecture, image transfer, SIF inspection, GPU visibility, CUDA/library,
Slurm, node, timeout-before-science, and checksum failures are classified as
`infrastructure_failed`. They do not invoke the scientific verifier and do not
produce an evaluated-Agent failure or reward.

After the fixed solution begins producing scientific artifacts, verifier
failure is classified separately as `scientific_failed`. Logs and receipts
record the boundary so a runtime failure cannot be mistaken for a scientific
result.

## Execution Order

1. Freeze the current arm64 lock as ineligible evidence.
2. Complete and validate the CPU-amd64 bridge image/SIF for diagnostics.
3. Build and transfer the GPU-amd64 image/archive/SIF.
4. Run the fixed A100 qualification and generate the shared runtime lock.
5. Generalize and test controller policy for 031–033.
6. Submit one diagnostic paper run with seed `2026081199`.
7. Only after diagnostic review, freeze code/runtime and schedule formal runs.

The current CPU 032 live-resume run remains diagnostic and cannot satisfy the
GPU qualification or formal evidence gates.
