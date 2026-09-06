"""Execution drivers: one contract, two backends (Slurm = Formal-qualified)."""

from ccbench.hpc.drivers.base import (
    DriverSelectionError,
    HpcDriver,
    resolve_driver,
)
from ccbench.hpc.drivers.compshare import CompShareDriver
from ccbench.hpc.drivers.process import ProcessDriver
from ccbench.hpc.drivers.routed import RoutedDriver
from ccbench.hpc.drivers.slurm import SlurmDriver

__all__ = [
    "CompShareDriver",
    "DriverSelectionError",
    "HpcDriver",
    "ProcessDriver",
    "RoutedDriver",
    "SlurmDriver",
    "resolve_driver",
]
