"""Executor registry: execution class contract -> executor.

The registry is the only place a runtime class is bound to a concrete
executor.  New execution classes extend the benchmark by registering here
(together with a case-contract entry in ``bench.contracts.case``);
no per-case code is touched.
"""

from __future__ import annotations

from typing import Type

from bench.executors.base import Executor
from bench.executors.hpc import HpcExecutor
from bench.executors.local import LocalExecutor

EXECUTORS: dict[str, Type[Executor]] = {
    "local_sandbox": LocalExecutor,
    "hpc_controller": HpcExecutor,
}


class ExecutorRegistry:
    """Resolves an execution class to its executor instance."""

    def __init__(self, executors: dict[str, Type[Executor]] | None = None) -> None:
        self._executors = dict(executors) if executors is not None else EXECUTORS

    def resolve(self, execution_class: str, **deps) -> Executor:
        """Instantiate the executor for a class, injecting composition-root
        dependencies (e.g. the trusted HpcDispatcher)."""
        try:
            cls = self._executors[execution_class]
        except KeyError:
            raise KeyError(
                f"unknown execution class {execution_class!r}; "
                f"known: {sorted(self._executors)}"
            ) from None
        return cls(**deps)

    def classes(self) -> tuple[str, ...]:
        return tuple(sorted(self._executors))


_registry = ExecutorRegistry()


def resolve(execution_class: str, **deps) -> Executor:
    """Module-level convenience over the default registry."""
    return _registry.resolve(execution_class, **deps)
