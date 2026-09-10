# Shared DeepMD-JAX CPU runtime

This is the reusable local/verifier runtime for every case that declares the
`deepmd-jax` or `dpmp` capability. It is not a per-paper image and contains no
case input, model, reference answer, verifier, API credential, or HPC client.

The image is deliberately CPU-only. It supports local contract checks,
deterministic verifier execution, and small smoke runs. Production GPU work is
submitted through the CompShare operator profile; this image is never presented
as evidence that a GPU trajectory ran.

Build and verify:

```bash
bash ../build.sh deepmd-jax
docker run --rm --network none bench-runtime-deepmd-jax:0.2-cpu
```

`requirements.lock` fixes registry artifacts with hashes. The DeepMD-JAX source
archive is copied from the reviewed GO-water runtime material. The JAX-MD
archive is an auditable source-only derivative containing the importable
package, build metadata, README, and licenses; upstream tests, notebooks, and
sample data are deliberately excluded. Both archives are checked by SHA-256 in
the build driver before use.
