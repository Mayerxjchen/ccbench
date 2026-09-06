# hidden-validation/ — the HIDDEN scientific reference for 034

The case is **coordinate-free**: `public/` holds only `system.json` and the agent
generates its own structure and first-principles data, so the expert's 190-frame
AIMD mother set is a fair out-of-training reference. The expert's unrelaxed
PACKMOL structure lives in `../expert-trajectory/initial/water64.xyz`
(reference-only, never agent input). Everything here is derived from
`../expert-trajectory/aimd/processed/aimd.xyz` by
`generator/generate_hidden_validation.py` — **no HPC CP2K run is required.**

## Files

| File | Role |
|------|------|
| `dft-validation.extxyz` | 190 labeled extxyz frames (energy eV + forces eV/Å + Lattice). Hidden E/F reference for verifier **L7**. |
| `rdf-reference.json` | O-O / O-H / H-H RDF first peaks + full curves from the same 190 frames. Reference for verifier **L9**. |
| `manifest.json` | Provenance: source file hashes, frame count, temperature window, generation params. |
| `generator/generate_hidden_validation.py` | Regenerates the above. Idempotent. |

## RDF first peaks (computed 2026-08-10)

| Pair | Peak / Å | Expert AIMD / Å |
|------|----------|-----------------|
| O–O | 2.81 | 2.80 |
| O–H | 0.99 | 0.98 |
| H–H | 1.57 | 1.58 |

## Staging

At verify time the harness copies `tests/` into the container as `/tests`. The
verifier reads `/tests/hidden/dft-validation.extxyz` and
`/tests/hidden/rdf-reference.json` (committed copies of the files here). They are
never placed in `/app`, and `system.json` / `HPC_ENVIRONMENT.md` / `instruction.md`
never reference their existence.
