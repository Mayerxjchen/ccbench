# CCBench Runtimes

This directory contains the canonical runtime definitions, qualification trust anchors, and pinned environment locks for CCBench.

## Directory Structure

- `locks/`: Cryptographically pinned runtime lock manifests (`ai2kit`, `cp2k`, `deepmd-jax`, `matclaw-cips`, etc.) declaring OCI digests, Python environments, and qualification artifacts.
- `recipes/`: Reproducible Docker / Apptainer recipe files and build instructions for candidate sandboxes and compute runtimes.
- `trust.toml`: Active cryptographic trust store specifying authorized verification keys for site qualification receipts.
