"""bench — trusted common core for the MLIP scientific-agent benchmark.

Split into three subpackages:

- ``bench.contracts`` — frozen case/result/run-record contracts.
- ``bench.core`` — packaging, lifecycle, quarantine, verifier,
  harness, and immutable run-store.
- ``bench.hpc`` — optional HPC extension: job contract, bench-hpc
  client, trusted gateway, adapters, and conformance.
"""

from .suite import inspect_suite, validate_suite

__all__ = ["inspect_suite", "validate_suite"]
__version__ = "0.1.0"
