"""ccbench — trusted common core for the MLIP scientific-agent benchmark.

Split into three subpackages:

- ``ccbench.contracts`` — frozen case/result/run-record contracts.
- ``ccbench.core`` — packaging, lifecycle, quarantine, verifier,
  harness, and immutable run-store.
- ``ccbench.hpc`` — optional HPC extension: job contract, bench-hpc
  client, trusted gateway, adapters, and conformance.
"""
