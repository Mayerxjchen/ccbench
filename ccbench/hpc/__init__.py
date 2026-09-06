"""ccbench.hpc — optional HPC extension (bench-hpc contract, gateway, adapters, conformance)."""

from ccbench.hpc.dispatcher import (
    DispatcherClosedError,
    DispatcherSession,
    HpcDispatcher,
)

__all__ = ["DispatcherClosedError", "DispatcherSession", "HpcDispatcher"]
