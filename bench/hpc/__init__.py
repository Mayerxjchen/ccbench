"""bench.hpc — optional HPC extension (bench-hpc contract, gateway, adapters, conformance)."""

from bench.hpc.dispatcher import (
    DispatcherClosedError,
    DispatcherSession,
    HpcDispatcher,
)

__all__ = ["DispatcherClosedError", "DispatcherSession", "HpcDispatcher"]
