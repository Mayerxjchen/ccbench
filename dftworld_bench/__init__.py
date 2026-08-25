"""dftworld_bench — trusted common core for the MLIP scientific-agent benchmark.

Split into three subpackages:

- ``dftworld_bench.contracts`` — frozen case/result/run-record contracts.
- ``dftworld_bench.core`` — packaging, lifecycle, quarantine, verifier,
  harness, and immutable run-store.
- ``dftworld_bench.hpc`` — optional HPC extension: job contract, bench-hpc
  client, trusted gateway, adapters, and conformance.
"""
