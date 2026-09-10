"""Case-agnostic executor registry (Task 11).

Resolve a case's declared ``execution_class`` to the executor that owns its
runtime lifecycle.  Nothing here dispatches on a case name or id.
"""

from bench.executors.base import ExecutionContext, Executor
from bench.executors.hpc import HpcExecutor
from bench.executors.local import LocalExecutor
from bench.executors.registry import EXECUTORS, ExecutorRegistry, resolve

__all__ = [
    "EXECUTORS",
    "ExecutionContext",
    "Executor",
    "ExecutorRegistry",
    "HpcExecutor",
    "LocalExecutor",
    "resolve",
]
