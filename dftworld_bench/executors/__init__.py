"""Case-agnostic executor registry (Task 11).

Resolve a case's declared ``execution_class`` to the executor that owns its
runtime lifecycle.  Nothing here dispatches on a case name or id.
"""

from dftworld_bench.executors.base import ExecutionContext, Executor
from dftworld_bench.executors.hpc import HpcExecutor
from dftworld_bench.executors.local import LocalExecutor
from dftworld_bench.executors.registry import EXECUTORS, ExecutorRegistry, resolve

__all__ = [
    "EXECUTORS",
    "ExecutionContext",
    "Executor",
    "ExecutorRegistry",
    "HpcExecutor",
    "LocalExecutor",
    "resolve",
]
