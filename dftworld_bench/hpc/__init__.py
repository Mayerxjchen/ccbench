"""dftworld_bench.hpc — optional HPC extension (bench-hpc contract, gateway, adapters, conformance)."""

from dftworld_bench.hpc.dispatcher import (
    DispatcherClosedError,
    DispatcherSession,
    HpcDispatcher,
)

__all__ = ["DispatcherClosedError", "DispatcherSession", "HpcDispatcher"]
