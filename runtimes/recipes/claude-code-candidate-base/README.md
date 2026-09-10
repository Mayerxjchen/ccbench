# Claude Code Candidate base

This image is the sole active Candidate runtime for every case. Bench starts
one fresh container per run and attaches it only to a Docker `--internal`
network. The model gateway sidecar is the only network peer; the real API key
remains on the host.

This shared control/scientific-utility base contains Claude Code, CPython
3.11.x, NumPy 1.26.4, ASE 3.26.0, Debian Packmol 20.14.0, and pinned shell/file
utilities. It deliberately contains no CP2K, LAMMPS, DeepMD, JAX, MPI, CUDA,
Slurm, CompShare, SSH, or Docker CLI; those belong to the operator-selected
environment/verifier runtime. ASE's runtime dependency closure is included in
the hash lock. Packmol is installed from Debian's architecture-native package;
this avoids host-specific compiler optimizations and keeps a Fortran toolchain
out of the final image.

Build with explicit immutable inputs:

```bash
./build.sh \
  --base-image-digest sha256:<verified-node-digest> \
  --claude-code-version <exact-version> \
  --python-deb-version 3.11.2-6+deb12u8 \
  --packmol-version 1:20.14.0-1
```

The current reproducible local inputs are `node:22-bookworm-slim` with its
verified `sha256` digest, Claude Code `2.1.266`, Python Debian package
`3.11.2-6+deb12u8`, and Debian Packmol `1:20.14.0-1`. The wrapper accepts exact
package versions when the Debian snapshot is updated.

The wrapper rejects missing or malformed digests, versions, and tags. A Formal
run must record the resulting image ID/digest in its receipt. Case task/input
and verifier files are mounted by Bench at run time; they are never baked
into this image. `requirements.lock` is hash-locked for Linux CPython 3.11
architectures and installed with `--no-deps`.

Regenerate the lock only after reviewing root version changes:

```bash
uv pip compile --universal --generate-hashes --no-header --python-version 3.11 \
  --output-file requirements.lock requirements.in
```

`smoke.sh` is also the qualification probe. It runs with no network, a
read-only root filesystem, all capabilities dropped, and a small writable
`/tmp`; it verifies real ASE file output and a real Packmol packing job.
