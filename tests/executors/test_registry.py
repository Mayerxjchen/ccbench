"""Case-agnostic executor registry (Task 11).

A new HPC case extends the benchmark by declaring ``[execution] class =
"hpc_controller"`` in its manifest — no shared-runtime edit.  The registry
resolves the declared execution class to an executor; nothing dispatches on a
case name or id.
"""

from __future__ import annotations

import types

import pytest

from dftworld_bench.executors import HpcExecutor, LocalExecutor


@pytest.fixture()
def registry():
    from dftworld_bench.executors.registry import ExecutorRegistry

    return ExecutorRegistry()


@pytest.fixture()
def dummy_case():
    case = types.SimpleNamespace()
    case.execution_class = "local_sandbox"
    case.gpus = 0
    return case


def test_new_hpc_case_needs_no_core_edit(registry, dummy_case):
    dummy_case.execution_class = "hpc_controller"
    from dftworld_bench.hpc.dispatcher import HpcDispatcher
    from dftworld_bench.hpc.gateway_runtime import GatewayRuntime

    executor = registry.resolve(
        dummy_case.execution_class,
        dispatcher=HpcDispatcher(GatewayRuntime(), {}),
    )
    assert isinstance(executor, HpcExecutor)


def test_local_sandbox_case_resolves_local_executor(registry, dummy_case):
    executor = registry.resolve(dummy_case.execution_class)
    assert isinstance(executor, LocalExecutor)


def test_unknown_execution_class_fails_closed(registry):
    with pytest.raises(KeyError, match="unknown execution class"):
        registry.resolve("kube")
