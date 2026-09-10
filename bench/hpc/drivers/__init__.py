"""Execution drivers: one contract, two backends (Slurm = Formal-qualified)."""

from bench.hpc.drivers.base import (
    DriverSelectionError,
    HpcDriver,
    resolve_driver,
)
from bench.hpc.drivers.compshare import CompShareDriver
from bench.hpc.drivers.process import ProcessDriver
from bench.hpc.drivers.routed import RoutedDriver
from bench.hpc.drivers.slurm import SlurmDriver

__all__ = [
    "CompShareDriver",
    "DriverSelectionError",
    "HpcDriver",
    "ProcessDriver",
    "RoutedDriver",
    "SlurmDriver",
    "resolve_driver",
]
