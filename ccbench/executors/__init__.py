"""Case-agnostic executor registry (Task 11).

Resolve a case's declared ``execution_class`` to the executor that owns its
runtime lifecycle.  Nothing here dispatches on a case name or id.
"""

from ccbench.executors.base import ExecutionContext, Executor
from ccbench.executors.hpc import HpcExecutor
from ccbench.executors.local import LocalExecutor
from ccbench.executors.registry import EXECUTORS, ExecutorRegistry, resolve

__all__ = [
    "EXECUTORS",
    "ExecutionContext",
    "Executor",
    "ExecutorRegistry",
    "HpcExecutor",
    "LocalExecutor",
    "resolve",
]
